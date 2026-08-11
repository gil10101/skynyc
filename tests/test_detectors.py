"""Detector fixture suite (PRD §7, ground rule 6).

Real sequences captured 2026-08-11 from the droplet's retained log
(scripts/capture_sequence.py); synthetic geometry only where weather has not
yet provided the pattern (tests/synthetic_tracks.py — disclosed there).

Tests drive the pure engine sample-by-sample — the worst-case micro-batch
granularity — so passing here holds under any Spark batching.
"""

import json
from pathlib import Path

from streaming.detectors import engine
from tests import synthetic_tracks

SEQ = Path(__file__).parent / "fixtures" / "sequences"


def load_messages(name: str) -> list[dict]:
    return json.loads((SEQ / f"{name}.json").read_text())["messages"]


def run_engine(messages: list[dict], *, final_timeout: bool = False) -> list[dict]:
    """Feed messages one at a time; optionally fire the state timeout at the end
    (Spark's ProcessingTimeTimeout after 90 s of silence)."""
    state = None
    events: list[dict] = []
    for message in messages:
        state, new_events = engine.process(state, [message])
        events.extend(new_events)
    if final_timeout:
        state, new_events = engine.process(state, [], timed_out=True)
        events.extend(new_events)
    return events


def only_types(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e["event_type"] == event_type]


class TestArrival:
    def test_baw04a_clean_arrival_jfk(self):
        # Real: 1539 m descent -> on_ground at JFK, then 20 min of taxi.
        events = run_engine(load_messages("arrival_jfk_baw04a"))
        arrivals = only_types(events, "arrival")
        assert len(arrivals) == 1, f"expected exactly one arrival, got {events}"
        a = arrivals[0]
        assert a["airport"] == "KJFK"
        assert 1786477600 <= a["event_ts"] <= 1786477900  # touchdown window
        assert a["icao24"] == "400776"

    def test_dlh402_clean_arrival_ewr(self):
        events = run_engine(load_messages("arrival_ewr_dlh402"))
        arrivals = only_types(events, "arrival")
        assert len(arrivals) == 1
        assert arrivals[0]["airport"] == "KEWR"

    def test_dal2602_coverage_loss_final_lga(self):
        # Real: last contact 83 m / 1.8 km from LGA — confirmed via timeout.
        events = run_engine(load_messages("coverage_loss_lga_dal2602"), final_timeout=True)
        arrivals = only_types(events, "arrival")
        assert len(arrivals) == 1
        assert arrivals[0]["airport"] == "KLGA"

    def test_overflight_emits_nothing(self):
        events = run_engine(load_messages("overflight_amx034"), final_timeout=True)
        assert events == []

    def test_taxi_only_emits_nothing(self):
        events = run_engine(load_messages("taxi_jfk_afr006"), final_timeout=True)
        assert events == []

    def test_arrival_dedupes_within_window(self):
        # Replaying the same landing back-to-back must not double-emit.
        messages = load_messages("arrival_jfk_baw04a")
        state = None
        events = []
        for message in messages + messages[-10:]:
            state, new = engine.process(state, [message])
            events.extend(new)
        assert len(only_types(events, "arrival")) == 1

    def test_event_id_deterministic(self):
        first = run_engine(load_messages("arrival_jfk_baw04a"))
        second = run_engine(load_messages("arrival_jfk_baw04a"))
        assert first[0]["event_id"] == second[0]["event_id"]


class TestHolding:
    def test_racetrack_airliner_detected(self):
        events = run_engine(synthetic_tracks.holding_racetrack(), final_timeout=True)
        holds = only_types(events, "holding")
        assert len(holds) == 1
        assert holds[0]["duration_s"] >= 300  # 2.5 orbits at ~200 s each
        assert holds[0]["airport"] in {"KJFK", "KLGA", "KEWR"}

    def test_ga_sightseeing_loop_rejected(self):
        # Light class + 55 m/s: category/velocity filters must reject.
        events = run_engine(synthetic_tracks.ga_sightseeing_loop(), final_timeout=True)
        assert only_types(events, "holding") == []

    def test_straight_transit_rejected(self):
        events = run_engine(load_messages("overflight_amx034"), final_timeout=True)
        assert only_types(events, "holding") == []


class TestGoAround:
    def test_balked_landing_detected(self):
        events = run_engine(synthetic_tracks.go_around())
        gas = only_types(events, "go_around")
        assert len(gas) == 1
        assert gas[0]["airport"] == "KJFK"

    def test_vrate_wobble_in_flare_rejected(self):
        # One positive-vrate blip with no altitude gain is a flare, not a GA.
        events = run_engine(synthetic_tracks.landing_with_vrate_wobble())
        assert only_types(events, "go_around") == []

    def test_real_arrivals_are_not_go_arounds(self):
        for name in ("arrival_jfk_baw04a", "arrival_ewr_dlh402"):
            events = run_engine(load_messages(name))
            assert only_types(events, "go_around") == [], name


class TestStateHygiene:
    def test_ring_buffer_bounded(self):
        state = None
        for message in load_messages("arrival_jfk_baw04a"):
            state, _ = engine.process(state, [message])
        assert len(state["samples"]) <= engine.RING_SIZE

    def test_timeout_clears_state(self):
        state = None
        for message in load_messages("overflight_amx034"):
            state, _ = engine.process(state, [message])
        state, _ = engine.process(state, [], timed_out=True)
        assert state is None


class TestFixtureProvenance:
    def test_synthetic_fixtures_are_tracked(self):
        # When a real holding/GA capture lands, add it here and retire the
        # synthetic twin. This test is the reminder.
        real = {p.stem for p in SEQ.glob("*.json")}
        assert "arrival_jfk_baw04a" in real
        synthetic_still_needed = {"holding", "go_around"} - {
            n.split("_")[1] for n in real if n.startswith("real_")
        }
        assert synthetic_still_needed, "retire tests/synthetic_tracks.py generators"
