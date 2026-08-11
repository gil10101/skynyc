"""NWS poller -> Kafka `wx.observations` + `wx.alerts` (PRD §5.2).

  - observations: /stations/{id}/observations/latest every 5 min per station,
    deduped on properties.timestamp — publish only when it changes (METARs are
    ~hourly; 5-min polling exists to catch SPECIs quickly)
  - alerts: /alerts/active?point=lat,lon every 2 min per airport, published when
    an alert is new or its (event, severity, effective, ends) signature changes
  - mandatory User-Agent "skynyc (<CONTACT_EMAIL>)" on every request — requests
    without it are rejected (Manual 01 §B)
  - 5xx/timeout -> shared exponential backoff; never crash on a bad poll
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time

import requests
from confluent_kafka import Producer

from producers.common.backoff import Backoff
from producers.common.models import WeatherAlertMessage
from producers.common.wx_parsing import parse_observation

log = logging.getLogger("weather_producer")

OBS_URL = "https://api.weather.gov/stations/{station}/observations/latest"
ALERTS_URL = "https://api.weather.gov/alerts/active"
OBS_TOPIC = "wx.observations"
ALERTS_TOPIC = "wx.alerts"
TIMEOUT_S = 15
OBS_PERIOD_S = 300  # 5 min per station (PRD §5.2)
ALERTS_PERIOD_S = 120  # 2 min per airport

# Airport reference points (PRD §7) — alerts are point queries at the field.
AIRPORTS: dict[str, tuple[float, float]] = {
    "KJFK": (40.6413, -73.7781),
    "KLGA": (40.7769, -73.8740),
    "KEWR": (40.6895, -74.1745),
}

_running = True


def _stop(signum: int, _frame: object) -> None:
    global _running
    log.info("signal %s — exiting", signum)
    _running = False


def _headers() -> dict[str, str]:
    contact = os.environ["CONTACT_EMAIL"]
    return {"User-Agent": f"skynyc ({contact})", "Accept": "application/geo+json"}


def _get(url: str, params: dict[str, str] | None, backoff: Backoff) -> dict | None:
    """GET with the shared failure policy. None = this poll yielded nothing."""
    try:
        response = requests.get(url, params=params, headers=_headers(), timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        backoff.sleep(f"{url}: {exc.__class__.__name__}")
        return None
    if response.status_code == 403:
        # Missing/blank User-Agent is the documented cause; fix is config, not retry.
        log.error("403 from NWS — check CONTACT_EMAIL / User-Agent (Manual 01 §B)")
        backoff.sleep("NWS 403")
        return None
    if response.status_code >= 500:
        backoff.sleep(f"NWS HTTP {response.status_code}")
        return None
    if response.status_code != 200:
        log.warning("unexpected HTTP %d from %s — skipping", response.status_code, url)
        return None
    backoff.reset()
    try:
        return response.json()
    except ValueError:
        log.warning("non-JSON body from %s — skipping", url)
        return None


def poll_observation(station: str, producer: Producer, last_obs_ts: dict[str, str],
                     backoff: Backoff) -> None:
    payload = _get(OBS_URL.format(station=station), None, backoff)
    if not payload:
        return
    properties = payload.get("properties") or {}
    obs_ts = properties.get("timestamp")
    if not obs_ts:
        log.warning("%s observation missing properties.timestamp — skipping", station)
        return
    if last_obs_ts.get(station) == obs_ts:
        log.info("%s obs unchanged at %s — dedupe skip", station, obs_ts)
        return
    message = parse_observation(station, properties)
    producer.produce(OBS_TOPIC, key=station, value=message.model_dump_json())
    producer.flush(10)
    last_obs_ts[station] = obs_ts
    log.info("%s obs published obs_ts=%s category=%s wind_kmh=%s vis_m=%s ceiling_ft=%s",
             station, obs_ts, message.flight_category, message.wind_speed_kmh,
             message.visibility_m, message.ceiling_ft)


def poll_alerts(airport: str, producer: Producer,
                seen: dict[tuple[str, str], tuple], backoff: Backoff) -> None:
    lat, lon = AIRPORTS[airport]
    payload = _get(ALERTS_URL, {"point": f"{lat},{lon}"}, backoff)
    if payload is None:
        return
    features = payload.get("features") or []
    published = 0
    for feature in features:
        properties = feature.get("properties") or {}
        alert_id = feature.get("id") or properties.get("id")
        if not alert_id:
            continue
        signature = (properties.get("event"), properties.get("severity"),
                     properties.get("effective"), properties.get("ends"))
        if seen.get((alert_id, airport)) == signature:
            continue  # unchanged — the topic carries changes, not heartbeats
        message = WeatherAlertMessage(
            alert_id=alert_id,
            airport=airport,
            event=properties.get("event"),
            severity=properties.get("severity"),
            effective_ts=properties.get("effective"),
            ends_ts=properties.get("ends"),
            raw=properties,
        )
        producer.produce(ALERTS_TOPIC, key=airport, value=message.model_dump_json())
        seen[(alert_id, airport)] = signature
        published += 1
    if published:
        producer.flush(10)
    log.info("%s alerts active=%d published=%d", airport, len(features), published)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    producer = Producer(
        {
            "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"),
            "acks": "all",
            "client.id": "skynyc-producer-weather",
        }
    )
    backoff = Backoff()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_obs_ts: dict[str, str] = {}
    seen_alerts: dict[tuple[str, str], tuple] = {}
    next_obs = 0.0
    next_alerts = 0.0
    log.info("polling NWS: obs every %ss, alerts every %ss for %s",
             OBS_PERIOD_S, ALERTS_PERIOD_S, list(AIRPORTS))

    while _running:
        now = time.monotonic()
        try:
            if now >= next_alerts:
                next_alerts = now + ALERTS_PERIOD_S
                for airport in AIRPORTS:
                    poll_alerts(airport, producer, seen_alerts, backoff)
            if now >= next_obs:
                next_obs = now + OBS_PERIOD_S
                for station in AIRPORTS:
                    poll_observation(station, producer, last_obs_ts, backoff)
        except Exception:
            log.exception("cycle failed — skipping (never crash on a bad poll)")
        time.sleep(min(max(next_obs - time.monotonic(), 0),
                       max(next_alerts - time.monotonic(), 0), 5))

    producer.flush(10)
    log.info("stopped cleanly")


if __name__ == "__main__":
    main()
