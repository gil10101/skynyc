"""Shared detector primitives (PRD §7).

Distances in km, altitudes in meters — OpenSky native. Conversions happen at
the edge (parsers), never inside a detector.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Airport reference points (PRD §7).
AIRPORTS: dict[str, tuple[float, float]] = {
    "KJFK": (40.6413, -73.7781),
    "KLGA": (40.7769, -73.8740),
    "KEWR": (40.6895, -74.1745),
}

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Sample:
    """One position report for one aircraft. lat/lon are guaranteed non-null
    (null-position rows never reach the detectors); everything else is
    nullable per the source and treated as a missing measurement."""

    ts: int
    lat: float
    lon: float
    alt: float | None
    vel: float | None
    track: float | None
    vrate: float | None
    on_ground: bool
    category: int | None
    callsign: str | None


def _null(value: float | None) -> float | None:
    """Missing measurement -> None. The Spark bridge delivers pandas records,
    where every null float arrives as NaN — which slips `is None` guards and
    compares False against every threshold, so it would walk through detector
    gates and poison the details JSON at the sink (ground rule 4). One nullable
    contract for both paths: NaN is null."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def from_message(message: dict) -> Sample | None:
    """Envelope dict -> Sample; None when the position is missing."""
    lat, lon = _null(message.get("lat")), _null(message.get("lon"))
    if lat is None or lon is None:
        return None
    return Sample(
        ts=int(message["api_ts"]),
        lat=float(lat),
        lon=float(lon),
        alt=_null(message.get("baro_alt_m")),
        vel=_null(message.get("velocity_ms")),
        track=_null(message.get("track_deg")),
        vrate=_null(message.get("vrate_ms")),
        on_ground=bool(_null(message.get("on_ground"))),
        category=_null(message.get("category")),
        callsign=(message.get("callsign") or None),
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest_airport(lat: float, lon: float) -> tuple[str, float]:
    """(airport code, distance km) for the closest of the three fields."""
    code = min(AIRPORTS, key=lambda k: haversine_km(lat, lon, *AIRPORTS[k]))
    return code, haversine_km(lat, lon, *AIRPORTS[code])


def track_delta_deg(a: float, b: float) -> float:
    """Signed shortest angular difference b-a in (-180, 180] — the unwrap step
    for cumulative-turn measurement across the 0/360 boundary."""
    d = (b - a + 180.0) % 360.0 - 180.0
    return d if d != -180.0 else 180.0


def latest_callsign(samples: list[Sample]) -> str | None:
    for sample in reversed(samples):
        if sample.callsign:
            return sample.callsign
    return None
