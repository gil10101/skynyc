"""Alert-shape contract over the recorded live /alerts/active response
(tests/fixtures/, captured 2026-08-11, includes a real Tsunami Warning). The
weather producer publishes exactly feature id + properties event/severity/
effective/ends; this pins the recorded shape that field mapping depends on, so
an upstream shape change fails a test instead of silently publishing nulls.
The producer module itself is never imported — it pulls in confluent_kafka at
module top, and the contract under test is the fixture's shape, not the
polling loop."""

import json
from datetime import datetime
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def alert_features() -> list:
    return load("nws_alerts_jfk.json")["features"]


# --- alerts envelope (PRD §5.2) ----------------------------------------------

class TestAlertsEnvelope:
    def test_features_is_a_list(self):
        payload = load("nws_alerts_jfk.json")
        assert isinstance(payload["features"], list)

    def test_capture_includes_the_tsunami_warning(self):
        # An empty capture would pin nothing — this recording holds two active
        # alerts, one of them the real Tsunami Warning.
        events = [feature["properties"]["event"] for feature in alert_features()]
        assert "Tsunami Warning" in events


# --- per-feature contract: the fields the producer publishes ------------------

class TestPublishedFieldContract:
    def test_id_is_a_non_empty_string(self):
        for feature in alert_features():
            assert isinstance(feature["id"], str)
            assert feature["id"]

    def test_event_is_a_string(self):
        for feature in alert_features():
            assert isinstance(feature["properties"]["event"], str)

    def test_severity_is_a_non_empty_string(self):
        # The exact vocabulary (Extreme/Severe/…) is NWS's to change — pin only
        # that severity arrives present and non-empty.
        for feature in alert_features():
            severity = feature["properties"]["severity"]
            assert isinstance(severity, str)
            assert severity

    def test_effective_parses_as_iso_timestamp(self):
        for feature in alert_features():
            effective = feature["properties"]["effective"]
            assert isinstance(datetime.fromisoformat(effective), datetime)

    def test_ends_is_null_or_iso_timestamp(self):
        # ends is nullable upstream (open-ended alerts); the key must still be
        # present — a vanished key would publish null ends_ts silently.
        for feature in alert_features():
            properties = feature["properties"]
            assert "ends" in properties
            ends = properties["ends"]
            assert ends is None or isinstance(datetime.fromisoformat(ends), datetime)
