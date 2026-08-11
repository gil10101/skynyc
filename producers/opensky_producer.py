"""OpenSky /states/all poller -> Kafka `adsb.states.raw` (PRD §5.1).

Behavior contract:
  - poll every POLL_SECONDS (30 s); degrade to 60 s while the states bucket is
    under CREDIT_DEGRADE_FLOOR (500) — 2,880 calls/day at 30 s leaves 28% headroom
  - one message per state vector, key = icao24 (per-aircraft ordering, PRD §6)
  - 401 -> refresh token once and retry; 429 -> sleep exactly the advertised
    X-Rate-Limit-Retry-After-Seconds; 5xx/timeout -> exponential backoff
  - never crash on a bad poll: skip the cycle and continue — gaps are data
  - X-Rate-Limit-Remaining is logged every call (`credits_remaining=` is the
    greppable field `make credits` reads)
"""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime, timezone

import requests
from confluent_kafka import Producer

from producers.common.backoff import Backoff
from producers.common.models import StateMessage
from producers.common.token_manager import TokenManager

log = logging.getLogger("opensky_producer")

STATES_URL = "https://opensky-network.org/api/states/all"
TOPIC = "adsb.states.raw"
TIMEOUT_S = 15
CREDIT_DEGRADE_FLOOR = 500
DEGRADED_POLL_S = 60

_running = True


def _stop(signum: int, _frame: object) -> None:
    global _running
    log.info("signal %s — finishing cycle and exiting", signum)
    _running = False


def bbox_params() -> dict[str, str]:
    lamin, lomin, lamax, lomax = (p.strip() for p in os.environ["BBOX"].split(","))
    return {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax, "extended": "1"}


def poll_once(tokens: TokenManager, params: dict[str, str]) -> tuple[requests.Response | None, str]:
    """One GET with the 401-refresh-retry-once rule. Returns (response, error)."""
    for attempt in (1, 2):
        try:
            response = requests.get(
                STATES_URL,
                params=params,
                headers={"Authorization": f"Bearer {tokens.get()}"},
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as exc:
            return None, f"request failed: {exc.__class__.__name__}: {exc}"
        if response.status_code == 401 and attempt == 1:
            log.warning("401 — refreshing token and retrying once")
            tokens.invalidate()
            continue
        return response, ""
    return None, "unreachable"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    tokens = TokenManager()
    params = bbox_params()
    poll_s = int(os.environ.get("POLL_SECONDS", "30"))
    producer = Producer(
        {
            "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"),
            "acks": "all",
            "linger.ms": 50,
            "client.id": "skynyc-producer-opensky",
        }
    )
    backoff = Backoff()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("polling %s every %ss over bbox %s", STATES_URL, poll_s, os.environ["BBOX"])

    while _running:
        cycle_start = time.monotonic()
        interval = poll_s
        try:
            response, error = poll_once(tokens, params)
            if response is None:
                backoff.sleep(error)
                continue

            remaining_raw = response.headers.get("X-Rate-Limit-Remaining")
            remaining = int(remaining_raw) if remaining_raw and remaining_raw.isdigit() else None

            if response.status_code == 429:
                retry_after = int(response.headers.get("X-Rate-Limit-Retry-After-Seconds", "60"))
                log.warning("429 credits exhausted — sleeping exactly %ss (PRD §5.1)", retry_after)
                time.sleep(retry_after)
                continue
            if response.status_code >= 500:
                backoff.sleep(f"HTTP {response.status_code}")
                continue
            if response.status_code != 200:
                backoff.sleep(f"unexpected HTTP {response.status_code}")
                continue
            backoff.reset()

            payload = response.json()
            api_ts = int(payload.get("time") or 0)
            poll_ts = int(datetime.now(tz=timezone.utc).timestamp())
            vectors = payload.get("states") or []

            published = 0
            null_pos = 0
            parse_drops = 0
            for vector in vectors:
                try:
                    message = StateMessage.from_state_vector(vector, poll_ts=poll_ts, api_ts=api_ts)
                except Exception:
                    parse_drops += 1  # drops are a metric, not an exception (ground rule 4)
                    continue
                if message.lat is None or message.lon is None:
                    null_pos += 1
                producer.produce(TOPIC, key=message.icao24, value=message.model_dump_json())
                producer.poll(0)
                published += 1
            producer.flush(10)

            if remaining is not None and remaining < CREDIT_DEGRADE_FLOOR:
                interval = DEGRADED_POLL_S
                log.warning("credit guard: remaining=%s < %s — degrading to %ss polling",
                            remaining, CREDIT_DEGRADE_FLOOR, DEGRADED_POLL_S)
            log.info(
                "poll ok n_states=%d published=%d null_pos=%d parse_drops=%d "
                "credits_remaining=%s interval=%ds",
                len(vectors), published, null_pos, parse_drops,
                remaining if remaining is not None else "?", interval,
            )
        except Exception:
            # Never crash on a bad poll — log it, skip the cycle, keep going.
            log.exception("cycle failed — skipping (gap is legitimate data)")

        elapsed = time.monotonic() - cycle_start
        time.sleep(max(interval - elapsed, 0))

    producer.flush(10)
    log.info("stopped cleanly")


if __name__ == "__main__":
    main()
