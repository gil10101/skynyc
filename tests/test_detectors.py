"""Detector fixture suite (PRD §7, ground rule 6).

Real sequences captured 2026-08-11 from the droplet's retained log
(scripts/capture_sequence.py); synthetic geometry only where weather has not
yet provided the pattern (tests/synthetic_tracks.py — disclosed there).

Tests drive the pure engine sample-by-sample, again as one coarse replay-sized
batch, and again through the encode/decode state seam the Spark bridge crosses
between micro-batches — emission must be identical on all three paths, so
passing here holds under any Spark batching and across serialized state.
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


def run_engine_through_seam(messages: list[dict], *, icao24: str,
                            final_timeout: bool = False) -> list[dict]:
    """run_engine, but with the Spark bridge's exact state lifecycle: the state
    JSON-round-trips through encode_state/decode_state between every message
    (one message per micro-batch), and the grouping key is passed explicitly on
    both the data and timeout paths."""
    state = None
    events: list[dict] = []
    for message in messages:
        state, new_events = engine.process(state, [message], icao24=icao24)
        state = engine.decode_state(engine.encode_state(state))
        events.extend(new_events)
    if final_timeout:
        state, new_events = engine.process(state, [], icao24=icao24, timed_out=True)
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


class TestSerializationSeam:
    # final_timeout mirrors the original test for each sequence: arrivals
    # confirm on ground contact; the rest need the 90 s silence to conclude.
    CASES = {
        "arrival_jfk_baw04a": False,
        "arrival_ewr_dlh402": False,
        "coverage_loss_lga_dal2602": True,
        "overflight_amx034": True,
        "taxi_jfk_afr006": True,
    }

    def test_seam_run_matches_in_memory_run(self):
        for name, final_timeout in self.CASES.items():
            messages = load_messages(name)
            icao24 = messages[0]["icao24"]
            in_memory = run_engine(messages, final_timeout=final_timeout)
            seam = run_engine_through_seam(messages, icao24=icao24,
                                           final_timeout=final_timeout)
            assert seam == in_memory, name
            assert [e["event_id"] for e in seam] == \
                [e["event_id"] for e in in_memory], name
            for event in seam:
                # Identity must survive the blob — a timeout-confirmed event
                # with empty icao24 corrupts event_id and ground-truth matching.
                assert event["icao24"] == icao24, name
                assert event["icao24"], name


class TestBatchGranularity:
    def test_coarse_batch_equivalence(self):
        # Replay/catch-up delivers whole tracks in one micro-batch; emission
        # must match the live 1-sample-per-batch cadence exactly.
        for name, track, event_type in (
            ("arrival_jfk_baw04a", load_messages("arrival_jfk_baw04a"), "arrival"),
            ("go_around", synthetic_tracks.go_around(), "go_around"),
        ):
            fine = run_engine(track)
            _, coarse = engine.process(None, list(track))
            assert coarse == fine, name
            [expected] = only_types(fine, event_type)
            [got] = only_types(coarse, event_type)
            assert (got["airport"], got["event_ts"], got["event_id"]) == (
                expected["airport"], expected["event_ts"], expected["event_id"]), name

    def test_event_time_gap_resets_state(self):
        # Coverage-loss approach, then the same aircraft seen again hours later
        # taxiing. DOCUMENTED MUTATION of recorded data: the taxi track's ts
        # values are shifted to open a 4 h event-time gap, and it borrows the
        # approach's icao24 because gap-reset is per-aircraft state. No external
        # timeout fires — on replay it never would; crossing the gap must
        # confirm the arrival exactly as live coverage loss does.
        approach = load_messages("coverage_loss_lga_dal2602")
        icao24 = approach[0]["icao24"]
        shift = (approach[-1]["api_ts"] + 4 * 3600) - load_messages("taxi_jfk_afr006")[0]["api_ts"]
        later_taxi = [
            {**m, "icao24": icao24,
             "api_ts": m["api_ts"] + shift, "poll_ts": m["poll_ts"] + shift}
            for m in load_messages("taxi_jfk_afr006")
        ]
        state = None
        events: list[dict] = []
        for message in approach + later_taxi:
            state, new = engine.process(state, [message])
            events.extend(new)
        arrivals = only_types(events, "arrival")
        assert len(arrivals) == 1, events
        assert arrivals[0]["airport"] == "KLGA"
        assert arrivals[0]["icao24"] == icao24
        assert arrivals[0]["details"]["confirmation"] == "coverage_loss"
        # Confirmed at the gap crossing, from the pre-gap short-final sample.
        assert arrivals[0]["event_ts"] == approach[-1]["api_ts"]
        assert events == arrivals  # the taxi portion contributes nothing


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
