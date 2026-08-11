"""Synthetic geometry generators for detector patterns not yet captured live.

DISCLOSED DEVIATION from the recorded-fixtures rule: holding patterns and
go-arounds are weather-driven and rare in the calm-VFR window collected so far.
These generators produce deterministic, geometrically-honest tracks (30 s
cadence, realistic speeds/altitudes, no fabricated API fields) so the detectors
have positive/negative cases from day one. Each is replaced by a captured real
sequence via scripts/capture_sequence.py the first time weather provides one —
tracked in tests/test_detectors.py::TestFixtureProvenance.
"""

from __future__ import annotations

import math

CADENCE_S = 30


def _msg(ts: int, lat: float, lon: float, alt: float | None, vel: float,
         track: float, vrate: float, on_ground: bool = False,
         icao24: str = "syn001", callsign: str = "SYN001", category: int = 4) -> dict:
    return {
        "poll_ts": ts, "api_ts": ts, "icao24": icao24, "callsign": callsign,
        "lon": lon, "lat": lat, "baro_alt_m": alt, "on_ground": on_ground,
        "velocity_ms": vel, "track_deg": track % 360, "vrate_ms": vrate,
        "category": category,
    }


def holding_racetrack(center_lat: float = 40.85, center_lon: float = -73.60,
                      alt: float = 2400.0, vel: float = 120.0,
                      turns: float = 2.5, category: int = 4,
                      icao24: str = "synhld") -> list[dict]:
    """Closed circling at holding altitude/speed: ~3.2-min orbits, net
    displacement ~0. 2.5 turns => cumulative track change 900° over ~8 min."""
    msgs = []
    ts = 1786480000
    radius_km = vel * 200 / 1000 / (2 * math.pi)  # one orbit per 200 s
    for i in range(int(turns * 200 / CADENCE_S) + 1):
        theta = 2 * math.pi * (i * CADENCE_S) / 200
        lat = center_lat + (radius_km / 111.0) * math.sin(theta)
        lon = center_lon + (radius_km / (111.0 * math.cos(math.radians(center_lat)))) * math.cos(theta)
        track = (math.degrees(theta) + 90.0) % 360
        msgs.append(_msg(ts + i * CADENCE_S, lat, lon, alt, vel, track, 0.0,
                         icao24=icao24, category=category))
    return msgs


def ga_sightseeing_loop() -> list[dict]:
    """Same circling geometry, but light-aircraft class and speed — the
    category/velocity filters must reject it."""
    return holding_racetrack(alt=900.0, vel=55.0, category=2, icao24="synga1")


def go_around(airport_lat: float = 40.6413, airport_lon: float = -73.7781,
              icao24: str = "synga2") -> list[dict]:
    """Final-approach descent to ~180 m, then a committed climb back to ~700 m
    with no ground contact — the balked-landing profile."""
    msgs = []
    ts = 1786480000
    # inbound descent from 11 km out, 750 m -> 180 m
    for i in range(8):
        frac = i / 7
        lat = airport_lat + (1 - frac) * 0.099  # ~11 km north, closing
        alt = 750 - frac * 570
        msgs.append(_msg(ts + i * CADENCE_S, lat, airport_lon, alt, 75.0, 180.0, -4.0))
    # climb-out: vrate strongly positive, altitude regained
    for j in range(6):
        lat = airport_lat + 0.004 + j * 0.010
        alt = 180 + (j + 1) * 110
        msgs.append(_msg(ts + (8 + j) * CADENCE_S, lat, airport_lon, alt, 85.0, 180.0, 6.0))
    return msgs


def landing_with_vrate_wobble(airport_lat: float = 40.6413,
                              airport_lon: float = -73.7781) -> list[dict]:
    """Normal landing whose vertical rate blips positive for ONE sample in the
    flare — must NOT be called a go-around (climb requires 2 consecutive +
    real altitude gain)."""
    msgs = []
    ts = 1786480000
    profile = [(0.080, 600, -4.0), (0.064, 470, -4.5), (0.048, 340, -3.8),
               (0.034, 230, -3.2), (0.022, 140, 3.5),  # the wobble
               (0.012, 70, -2.8)]
    for i, (dlat, alt, vr) in enumerate(profile):
        msgs.append(_msg(ts + i * CADENCE_S, airport_lat + dlat, airport_lon,
                         alt, 70.0, 180.0, vr))
    ts2 = ts + len(profile) * CADENCE_S
    for j in range(3):
        msgs.append(_msg(ts2 + j * CADENCE_S, airport_lat + 0.004 - j * 0.002,
                         airport_lon, None, 8.0, 180.0, None, on_ground=True))
    return msgs
