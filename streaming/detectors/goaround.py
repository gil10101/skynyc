"""Go-around detector (PRD §7): a balked landing.

Within 12 km of a field, below 800 m, descending (vrate < −2 for ≥ 2 samples),
then climbing (vrate > +3 for ≥ 2 consecutive samples) with ≥ 200 m altitude
gain and no ground contact in between.

The two-consecutive-climb + real-gain requirements are what separate a
go-around from a one-sample vertical-rate wobble in the landing flare.
"""

from __future__ import annotations

from streaming.detectors.common import Sample, nearest_airport

NEAR_DIST_KM = 12.0
LOW_ALT_M = 800.0
DESCENT_VRATE_MS = -2.0
DESCENT_MIN_SAMPLES = 2
CLIMB_VRATE_MS = 3.0
CLIMB_MIN_SAMPLES = 2
GAIN_MIN_M = 200.0
DEDUPE_S = 20 * 60


def _near_low(sample: Sample) -> tuple[str, float] | None:
    if sample.on_ground or sample.alt is None:
        return None
    airport, dist = nearest_airport(sample.lat, sample.lon)
    if dist < NEAR_DIST_KM and sample.alt < LOW_ALT_M:
        return airport, dist
    return None


def check(samples: list[Sample], flags: dict) -> dict | None:
    """Pattern scan over the ring: ...descent-run..., climb-run ending now."""
    if len(samples) < DESCENT_MIN_SAMPLES + CLIMB_MIN_SAMPLES:
        return None

    # Trailing climb run (consecutive, airborne, strong positive vrate).
    climb: list[Sample] = []
    for sample in reversed(samples):
        if (not sample.on_ground and sample.vrate is not None
                and sample.vrate > CLIMB_VRATE_MS):
            climb.append(sample)
        else:
            break
    climb.reverse()
    if len(climb) < CLIMB_MIN_SAMPLES:
        return None

    before = samples[: len(samples) - len(climb)]
    if any(s.on_ground for s in before[-6:]):
        return None  # touched down: that is a landing (and maybe a departure), not a GA

    # Descent run near the field immediately before the climb.
    descent = [
        s for s in before[-6:]
        if s.vrate is not None and s.vrate < DESCENT_VRATE_MS and _near_low(s)
    ]
    if len(descent) < DESCENT_MIN_SAMPLES:
        return None

    low_point = min((s for s in descent + climb if s.alt is not None),
                    key=lambda s: s.alt)
    top = climb[-1]
    if top.alt is None or low_point.alt is None:
        return None
    if top.alt - low_point.alt < GAIN_MIN_M:
        return None

    near = _near_low(descent[-1])
    if near is None:
        return None
    airport, dist = near

    window_start = climb[0].ts
    last = flags.get("last_go_around", {}).get(airport)
    if last is not None and (window_start - last) < DEDUPE_S:
        return None
    flags.setdefault("last_go_around", {})[airport] = window_start
    return {
        "event_type": "go_around",
        "airport": airport,
        "event_ts": window_start,
        "window_start": window_start,
        "duration_s": None,
        "details": {"low_alt_m": round(low_point.alt, 1),
                    "gain_m": round(top.alt - low_point.alt, 1),
                    "dist_km": round(dist, 2)},
    }
