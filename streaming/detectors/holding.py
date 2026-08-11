"""Holding detector (PRD §7): sustained turning without displacement.

Open when cumulative unwrapped |Δtrack| ≥ 340° accrues within ≤ 480 s inside
the altitude/speed band with net displacement < 15 km; extend while turning
persists; close after 120 s below the turn rate; emit once, with duration.

SPEC DEVIATION (flagged): PRD filters to category ∈ {3,4,5,6}. Live reality
2026-08-11: 94% of traffic reports category 0 (no information) — including
mainline airliners — so the allowlist would blind the detector. Implemented as
an EXCLUDE list of known light/GA classes instead; the 90 m/s velocity floor
already excludes light aircraft regardless of what they report.
"""

from __future__ import annotations

from streaming.detectors.common import Sample, haversine_km, nearest_airport, track_delta_deg

ALT_MIN_M = 1700.0
ALT_MAX_M = 5000.0
VEL_MIN_MS = 90.0
VEL_MAX_MS = 160.0
CUM_TRACK_DEG = 340.0
ACCRUE_WINDOW_S = 480
NET_DISPLACEMENT_MAX_KM = 15.0
CLOSE_AFTER_S = 120
# Known light/GA/rotor classes (OpenSky category, extended=1). 0/1 = unknown,
# deliberately allowed — see module docstring.
CATEGORY_EXCLUDE = {2, 8, 9, 10, 11, 12, 14}
# "Turning persists" = at least the opening turn rate over the recent window.
TURN_RATE_MIN_DEG_S = CUM_TRACK_DEG / ACCRUE_WINDOW_S


def _in_band(sample: Sample) -> bool:
    if sample.on_ground or sample.alt is None or sample.vel is None or sample.track is None:
        return False
    if sample.category in CATEGORY_EXCLUDE:
        return False
    return ALT_MIN_M <= sample.alt <= ALT_MAX_M and VEL_MIN_MS <= sample.vel <= VEL_MAX_MS


def _cum_turn(samples: list[Sample]) -> float:
    total = 0.0
    for previous, current in zip(samples, samples[1:]):
        total += abs(track_delta_deg(previous.track, current.track))
    return total


def _recent_turn_rate(samples: list[Sample], now: int) -> float:
    recent = [s for s in samples if s.track is not None and now - s.ts <= CLOSE_AFTER_S]
    if len(recent) < 2:
        return 0.0
    span = recent[-1].ts - recent[0].ts
    return _cum_turn(recent) / span if span > 0 else 0.0


def _close(flags: dict, samples: list[Sample], end_ts: int) -> dict | None:
    hold = flags.pop("holding", None)
    if not hold:
        return None
    duration = end_ts - hold["start_ts"]
    if duration <= 0:
        return None
    mid = [s for s in samples if hold["start_ts"] <= s.ts <= end_ts] or samples
    center = mid[len(mid) // 2]
    airport, dist = nearest_airport(center.lat, center.lon)
    return {
        "event_type": "holding",
        "airport": airport,
        "event_ts": hold["start_ts"],
        "window_start": hold["start_ts"],
        "duration_s": duration,
        "details": {"cum_track_deg": round(hold["cum_deg"], 0),
                    "dist_to_airport_km": round(dist, 1)},
    }


def check(samples: list[Sample], flags: dict) -> dict | None:
    if not samples:
        return None
    now = samples[-1].ts
    hold = flags.get("holding")

    if hold:
        rate = _recent_turn_rate(samples, now)
        if rate >= TURN_RATE_MIN_DEG_S and _in_band(samples[-1]):
            hold["last_turn_ts"] = now
            hold["cum_deg"] = hold["cum_deg"] + _cum_turn(samples[-2:])
            return None
        if now - hold["last_turn_ts"] >= CLOSE_AFTER_S:
            return _close(flags, samples, hold["last_turn_ts"])
        return None

    window = [s for s in samples if now - s.ts <= ACCRUE_WINDOW_S and _in_band(s)]
    if len(window) < 4:
        return None
    cum = _cum_turn(window)
    if cum < CUM_TRACK_DEG:
        return None
    displacement = haversine_km(window[0].lat, window[0].lon,
                                window[-1].lat, window[-1].lon)
    if displacement >= NET_DISPLACEMENT_MAX_KM:
        return None
    flags["holding"] = {"start_ts": window[0].ts, "last_turn_ts": now, "cum_deg": cum}
    return None  # emitted once, on close


def on_timeout(samples: list[Sample], flags: dict) -> dict | None:
    """Contact lost with a hold open: close it at the last turning sample."""
    hold = flags.get("holding")
    if not hold:
        return None
    return _close(flags, samples, hold["last_turn_ts"])
