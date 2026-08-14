"""Vercel Blob fallback publisher (public-dashboard spec §3.4).

The frontend falls back to these blobs when the API is unreachable — including
after the droplet is gone, which is why history.json carries canned
default-timeframe versions of everything the page needs. Without a token the
publishers no-op with one startup warning (same contract as the backup asset:
local dev must never fail on missing cloud config).

live.json    — every LIVE_INTERVAL_S, snapshot + status
history.json — at startup and daily at HISTORY_HOUR_UTC

Cadence is budget-bound: every PUT is a Vercel Blob "advanced operation" and
the free tier includes 2,000/month. At 60 s the live publisher alone burned
~1,440/day; hourly keeps the whole publisher near ~800/month. The blob is
only ever read in frozen mode (API unreachable), where an up-to-an-hour-old
capture is honest — the banner timestamps it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import requests

from api import queries_history as hist
from api import queries_live as live

log = logging.getLogger("api.blob")

BLOB_ENDPOINT = "https://blob.vercel-storage.com"
LIVE_INTERVAL_S = 3600
HISTORY_HOUR_UTC = 6  # 06:10Z, after the nightly batch world has settled
TIMEOUT_S = 10

HISTORY_KEYS = (
    "staircase", "causes", "seasonality", "worst", "monthly",
    "quality", "arrivals", "airborne", "wind",
)

# Canned default-timeframe sources — what the page shows when filters are dead.
_history_sources = {
    "staircase": lambda: hist.staircase(1987, 2100),
    "causes": hist.causes,
    "seasonality": hist.seasonality,
    "worst": lambda: hist.worst(10, "wx_cancelled"),
    "monthly": lambda: hist.monthly(None, None),
    "quality": lambda: live.quality(30),
    "arrivals": lambda: live.arrivals(24),
    "airborne": lambda: live.airborne(24),
    "wind": lambda: live.wind(24),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def put_json(path: str, payload: dict) -> bool:
    """PUT one JSON blob at a stable path. Returns success; never raises —
    fallback publishing must not disturb the API serving live traffic."""
    token = os.environ.get("BLOB_RW_TOKEN")
    if not token:
        return False
    try:
        response = requests.put(
            f"{BLOB_ENDPOINT}/{path}",
            data=json.dumps(payload, default=str).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "x-api-version": "7",
                "x-add-random-suffix": "0",
                "x-cache-control-max-age": "60",
                "content-type": "application/json",
            },
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        return True
    except requests.RequestException:
        log.warning("blob put %s failed", path, exc_info=True)
        return False


def history_bundle() -> dict:
    """All canned slices; one failing query costs its slice, not the bundle."""
    data: dict = {}
    for key in HISTORY_KEYS:
        try:
            data[key] = _history_sources[key]()
        except Exception:
            log.warning("history slice %s failed", key, exc_info=True)
            data[key] = None
    return {"generated_at": _now(), "data": data}


def publish_live() -> bool:
    snap = live.live_snapshot()
    return put_json("live.json", snap)


def publish_history() -> bool:
    return put_json("history.json", history_bundle())


async def publisher_loop() -> None:
    if not os.environ.get("BLOB_RW_TOKEN"):
        log.warning("BLOB_RW_TOKEN not set — blob fallback publishing disabled")
        return
    await asyncio.to_thread(publish_history)  # canned history from the start
    last_history_day = datetime.now(timezone.utc).date()
    while True:
        await asyncio.to_thread(publish_live)
        now = datetime.now(timezone.utc)
        if now.hour >= HISTORY_HOUR_UTC and now.date() > last_history_day:
            if await asyncio.to_thread(publish_history):
                last_history_day = now.date()
        await asyncio.sleep(LIVE_INTERVAL_S)
