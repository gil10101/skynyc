"""Detector engine — pure Python, no Spark dependency (PRD §7 Q3 core).

The Spark query is a thin wrapper around process(); everything decision-shaped
lives here so the fixture suite exercises the exact production logic.

State shape (JSON-serializable):
  {"samples": [sample tuples...], "flags": {...detector scratch...}}
Ring-bounded, expired by Spark's processing-time timeout (90 s): bounded state
is a correctness requirement, not an optimization (Manual 04 — memory growth
means a detector failed to time out).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from streaming.detectors import arrival, goaround, holding
from streaming.detectors.common import Sample, from_message, latest_callsign

RING_SIZE = 20
STATE_TIMEOUT_MS = 90_000  # PRD §7: contact lost = 90 s of silence


def event_id(icao24: str, event_type: str, window_start: int) -> str:
    """Deterministic identity (ground rule 7): replays land on the same row."""
    return hashlib.sha1(f"{icao24}|{event_type}|{window_start}".encode()).hexdigest()[:16]


def _sample_to_list(s: Sample) -> list:
    return [s.ts, s.lat, s.lon, s.alt, s.vel, s.track, s.vrate,
            s.on_ground, s.category, s.callsign]


def _sample_from_list(v: list) -> Sample:
    return Sample(ts=v[0], lat=v[1], lon=v[2], alt=v[3], vel=v[4], track=v[5],
                  vrate=v[6], on_ground=v[7], category=v[8], callsign=v[9])


def encode_state(state: dict | None) -> str:
    return json.dumps({
        "samples": [_sample_to_list(s) for s in state["samples"]],
        "flags": state["flags"],
    }) if state else ""


def decode_state(raw: str | None) -> dict | None:
    if not raw:
        return None
    decoded = json.loads(raw)
    return {"samples": [_sample_from_list(v) for v in decoded["samples"]],
            "flags": decoded["flags"]}


def _finish(icao24: str, samples: list[Sample], found: dict | None) -> list[dict]:
    if not found:
        return []
    return [{
        "event_id": event_id(icao24, found["event_type"], found["window_start"]),
        "icao24": icao24,
        "callsign": latest_callsign(samples),
        "event_type": found["event_type"],
        "airport": found["airport"],
        "event_ts": found["event_ts"],
        "duration_s": found["duration_s"],
        "details": found["details"],
    }]


def process(state: dict | None, messages: list[dict[str, Any]],
            *, timed_out: bool = False) -> tuple[dict | None, list[dict]]:
    """Advance one aircraft's state with new envelope messages.

    Returns (new_state, events). new_state None means remove the state —
    after a timeout the aircraft starts fresh on its next appearance.
    """
    if timed_out:
        if state is None:
            return None, []
        samples, flags = state["samples"], state["flags"]
        icao24 = state.get("icao24", "")
        events = _finish(icao24, samples, arrival.on_timeout(samples, flags))
        events += _finish(icao24, samples, holding.on_timeout(samples, flags))
        return None, events

    incoming = [s for m in messages if (s := from_message(m)) is not None]
    if not incoming:
        return state, []
    icao24 = messages[0]["icao24"]

    if state is None:
        state = {"icao24": icao24, "samples": [], "flags": {}}
    state.setdefault("icao24", icao24)

    seen = {s.ts for s in state["samples"]}
    state["samples"].extend(s for s in incoming if s.ts not in seen)
    state["samples"].sort(key=lambda s: s.ts)
    state["samples"] = state["samples"][-RING_SIZE:]

    samples, flags = state["samples"], state["flags"]
    events: list[dict] = []
    # Each detector is exception-transparent by design: a crash here must fail
    # the batch loudly, not degrade silently (skill: no swallowed errors).
    events += _finish(icao24, samples, arrival.check(samples, flags))
    events += _finish(icao24, samples, holding.check(samples, flags))
    events += _finish(icao24, samples, goaround.check(samples, flags))
    return state, events
