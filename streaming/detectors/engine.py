"""Detector engine — pure Python, no Spark dependency (PRD §7 Q3 core).

The Spark query is a thin wrapper around process(); everything decision-shaped
lives here so the fixture suite exercises the exact production logic.

State shape (JSON-serializable):
  {"icao24": ..., "samples": [sample tuples...], "flags": {...detector scratch...}}
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
# Event-time twin of STATE_TIMEOUT_MS: replay batches arrive seconds apart in
# wall time, so the processing-time timeout never fires between them — the same
# 90 s coverage-loss boundary must be enforced on sample timestamps, or a ring
# bridges hours of silence and fakes continuity.
GAP_RESET_S = STATE_TIMEOUT_MS // 1000


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
        # icao24 rides in the blob because the timeout path has no messages to
        # recover it from — without it, timeout-confirmed events lose their
        # identity and event_id/ground-truth matching silently corrupt.
        "icao24": state.get("icao24", ""),
        "samples": [_sample_to_list(s) for s in state["samples"]],
        "flags": state["flags"],
    }) if state else ""


def decode_state(raw: str | None) -> dict | None:
    if not raw:
        return None
    decoded = json.loads(raw)
    # .get: blobs written before icao24 was serialized must decode, not crash.
    return {"icao24": decoded.get("icao24", ""),
            "samples": [_sample_from_list(v) for v in decoded["samples"]],
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


def _timeout_events(icao24: str, samples: list[Sample], flags: dict) -> list[dict]:
    """Coverage-loss evaluation — shared by the state timeout (live) and the
    event-time gap reset (replay), which must confirm the same arrivals."""
    events = _finish(icao24, samples, arrival.on_timeout(samples, flags))
    events += _finish(icao24, samples, holding.on_timeout(samples, flags))
    return events


def process(state: dict | None, messages: list[dict[str, Any]],
            *, icao24: str = "", timed_out: bool = False) -> tuple[dict | None, list[dict]]:
    """Advance one aircraft's state with new envelope messages.

    icao24 is the caller's grouping key and outranks both the state blob and
    the message payload: the timeout path has no messages, and a blob written
    before icao24 was serialized carries no identity at all.

    Samples are applied one at a time in ts order so emission is identical at
    any batch granularity, and an event-time gap > GAP_RESET_S inside a batch
    behaves exactly like live coverage loss (timeout-confirm, then start fresh).

    Returns (new_state, events). new_state None means remove the state —
    after a timeout the aircraft starts fresh on its next appearance.
    """
    if timed_out:
        if state is None:
            return None, []
        return None, _timeout_events(icao24 or state.get("icao24", ""),
                                     state["samples"], state["flags"])

    incoming = [s for m in messages if (s := from_message(m)) is not None]
    if not incoming:
        return state, []
    incoming.sort(key=lambda s: s.ts)
    icao24 = icao24 or messages[0].get("icao24") or (state.get("icao24", "") if state else "")

    if state is None:
        state = {"samples": [], "flags": {}}
    state["icao24"] = icao24

    events: list[dict] = []
    emitted: set[str] = set()
    # One sample at a time regardless of batch size: a replay batch can carry
    # hours of one aircraft's history, and appending it wholesale trims the
    # ring straight through landings before the detectors ever look.
    for sample in incoming:
        ring = state["samples"]
        if ring and sample.ts - ring[-1].ts > GAP_RESET_S:
            found = _timeout_events(icao24, ring, state["flags"])
            state["samples"], state["flags"] = [], {}
        else:
            found = []
        if all(sample.ts != s.ts for s in state["samples"]):
            state["samples"].append(sample)
            state["samples"].sort(key=lambda s: s.ts)
            state["samples"] = state["samples"][-RING_SIZE:]
        samples, flags = state["samples"], state["flags"]
        # Each detector is exception-transparent by design: a crash here must
        # fail the batch loudly, not degrade silently (skill: no swallowed
        # errors).
        found += _finish(icao24, samples, arrival.check(samples, flags))
        found += _finish(icao24, samples, holding.check(samples, flags))
        found += _finish(icao24, samples, goaround.check(samples, flags))
        # Within-call dedupe by event_id backs the detectors' flag-based
        # cross-call dedupe: one batch replaying a window must not double-emit.
        for event in found:
            if event["event_id"] not in emitted:
                emitted.add(event["event_id"])
                events.append(event)
    return state, events
