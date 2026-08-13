"""SkyNYC public API (public-dashboard spec §3.2).

GET-only; database access through the skynyc_ro role; per-executor TTL caches
upstream of these routes. Caddy terminates TLS in front (spec §3.3). Single
uvicorn worker by design — the TTL caches and SSE fan-out are process-local.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from api import blob
from api import queries_history as hist
from api import queries_live as live

log = logging.getLogger("api")

ALLOWED_ORIGINS = [
    "https://sky.gillu.me",
    "https://gillu.me",
    "http://localhost:3002",  # web/ dev server
]

STATUS_FRESH_S = 120
STATUS_STALE_S = 900
SSE_INTERVAL_S = 10
SSE_MAX_PER_IP = 4

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.get_event_loop().create_task(_sampler_loop()),
        asyncio.get_event_loop().create_task(blob.publisher_loop()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
    max_age=3600,
)


def _run(fn, *args, **kwargs):
    """Executor call with the 400-on-ValueError contract (validation lives in
    the query layer; routes stay thin)."""
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _cached(payload: dict, max_age: int) -> JSONResponse:
    return JSONResponse(
        payload, headers={"Cache-Control": f"public, max-age={max_age}"}
    )


def _airports(airport: str | None) -> tuple[str, ...] | None:
    return tuple(airport.split(",")) if airport else None


# --- live --------------------------------------------------------------------

@app.get("/v1/snapshot")
def snapshot():
    return _cached(_run(live.live_snapshot), 5)


@app.get("/v1/arrivals")
def arrivals(hours: int = 24, airport: str | None = None):
    return _cached(_run(live.arrivals, hours, _airports(airport)), 30)


@app.get("/v1/airborne")
def airborne(hours: int = 24, airport: str | None = None):
    return _cached(_run(live.airborne, hours, _airports(airport)), 30)


@app.get("/v1/events")
def events(type: str | None = None, airport: str | None = None, limit: int = 50):
    return _cached(_run(live.recent_events, type, airport, limit), 30)


@app.get("/v1/wind")
def wind(hours: int = 24):
    return _cached(_run(live.wind, hours), 30)


@app.get("/v1/scatter")
def scatter(days: int = 7):
    return _cached(_run(live.scatter, days), 300)


@app.get("/v1/quality")
def quality(days: int = 30):
    return _cached(_run(live.quality, days), 300)


# --- history -----------------------------------------------------------------

@app.get("/v1/history/staircase")
def history_staircase(from_year: int = 1987, to_year: int = 2100):
    return _cached(_run(hist.staircase, from_year, to_year), 600)


@app.get("/v1/history/causes")
def history_causes():
    return _cached(_run(hist.causes), 600)


@app.get("/v1/history/seasonality")
def history_seasonality():
    return _cached(_run(hist.seasonality), 600)


@app.get("/v1/history/worst")
def history_worst(limit: int = 10, metric: str = "wx_cancelled"):
    return _cached(_run(hist.worst, limit, metric), 600)


@app.get("/v1/history/monthly")
def history_monthly(category: str | None = None, airport: str | None = None):
    return _cached(_run(hist.monthly, category, airport), 600)


# --- meta + stream -----------------------------------------------------------

@app.get("/v1/meta")
def meta():
    snap = _run(live.live_snapshot)
    freshness = snap["freshness_s"] if snap["freshness_s"] is not None else 10**9
    status = (
        "fresh" if freshness < STATUS_FRESH_S
        else "stale" if freshness < STATUS_STALE_S
        else "offline"
    )
    return _cached(
        {
            "generated_at": snap["generated_at"],
            "status": status,
            "freshness_s": freshness,
            "aircraft": len(snap["positions"]),
        },
        5,
    )


async def sampler_tick() -> None:
    """One refresh of the shared snapshot. Failures keep the last good value —
    a DB blip must not kill the stream; stale-and-labeled beats dead."""
    try:
        app.state.latest_snapshot = await asyncio.to_thread(live.live_snapshot)
    except Exception:
        log.exception("sampler tick failed")


async def _sampler_loop() -> None:
    while True:
        await sampler_tick()
        await asyncio.sleep(SSE_INTERVAL_S)


_sse_clients: dict[str, int] = {}


@app.get("/v1/stream")
@limiter.exempt
async def stream(request: Request):
    ip = get_remote_address(request)
    if _sse_clients.get(ip, 0) >= SSE_MAX_PER_IP:
        raise HTTPException(status_code=429, detail="too many streams from this address")

    async def gen():
        _sse_clients[ip] = _sse_clients.get(ip, 0) + 1
        try:
            while True:
                if await request.is_disconnected():
                    return
                snap = getattr(app.state, "latest_snapshot", None)
                if snap is None:
                    snap = await asyncio.to_thread(live.live_snapshot)
                yield f"event: snap\ndata: {json.dumps(snap, default=str)}\n\n"
                await asyncio.sleep(SSE_INTERVAL_S)
        finally:
            _sse_clients[ip] -= 1

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
