"""Arrival detector (PRD §7).

Candidate: dist(airport) < 7 km AND alt < 1,000 m AND descending.
Confirmed:  on_ground within 4 km — or contact lost (state timeout) with the
last sample under 450 m within 5 km: short-final coverage loss is a verified
live pattern, not an edge case.
Dedupe: one arrival per aircraft per airport per 20 minutes.
"""

from __future__ import annotations

from streaming.detectors.common import Sample, nearest_airport

# Tuning constants — M3's replay loop edits these; keep them named and alone.
CANDIDATE_DIST_KM = 7.0
CANDIDATE_ALT_M = 1000.0
CONFIRM_GROUND_DIST_KM = 4.0
LOSS_ALT_M = 450.0
LOSS_DIST_KM = 5.0
DEDUPE_S = 20 * 60


def _is_candidate(sample: Sample) -> bool:
    if sample.on_ground or sample.alt is None:
        return False
    if sample.alt >= CANDIDATE_ALT_M:
        return False
    if sample.vrate is not None and sample.vrate >= 0:
        return False
    _, dist = nearest_airport(sample.lat, sample.lon)
    return dist < CANDIDATE_DIST_KM


def _deduped(flags: dict, airport: str, ts: int) -> bool:
    last = flags.get("last_arrival", {}).get(airport)
    return last is not None and (ts - last) < DEDUPE_S


def _mark(flags: dict, airport: str, ts: int) -> None:
    flags.setdefault("last_arrival", {})[airport] = ts


def check(samples: list[Sample], flags: dict) -> dict | None:
    """Ground-contact confirmation: airborne earlier in the window, on_ground
    now, within 4 km of a field. Returns event kwargs or None."""
    if len(samples) < 2:
        return None
    last = samples[-1]
    if not last.on_ground:
        return None
    if not any(not s.on_ground for s in samples[:-1]):
        return None  # taxi-only window: never seen airborne
    airport, dist = nearest_airport(last.lat, last.lon)
    if dist >= CONFIRM_GROUND_DIST_KM:
        return None
    if _deduped(flags, airport, last.ts):
        return None
    # window_start: first candidacy sample of the approach (falls back to the
    # last airborne sample) — deterministic from the ring content.
    window_start = next(
        (s.ts for s in samples if _is_candidate(s)),
        next(s.ts for s in reversed(samples) if not s.on_ground),
    )
    _mark(flags, airport, last.ts)
    return {
        "event_type": "arrival",
        "airport": airport,
        "event_ts": last.ts,
        "window_start": window_start,
        "duration_s": None,
        "details": {"confirmation": "on_ground", "dist_km": round(dist, 2)},
    }


def on_timeout(samples: list[Sample], flags: dict) -> dict | None:
    """Coverage-loss confirmation, evaluated when the 90 s state timeout fires:
    last known sample low and close means the track ended on short final."""
    if not samples:
        return None
    last = samples[-1]
    if last.on_ground or last.alt is None or last.alt >= LOSS_ALT_M:
        return None
    airport, dist = nearest_airport(last.lat, last.lon)
    if dist >= LOSS_DIST_KM:
        return None
    if _deduped(flags, airport, last.ts):
        return None
    window_start = next((s.ts for s in samples if _is_candidate(s)), last.ts)
    _mark(flags, airport, last.ts)
    return {
        "event_type": "arrival",
        "airport": airport,
        "event_ts": last.ts,
        "window_start": window_start,
        "duration_s": None,
        "details": {"confirmation": "coverage_loss",
                    "last_alt_m": round(last.alt, 1), "dist_km": round(dist, 2)},
    }
