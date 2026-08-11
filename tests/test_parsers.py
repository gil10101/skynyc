"""Parser tests over recorded live responses (tests/fixtures/, captured
2026-08-11). Fixtures are real API output — the null patterns and unitCodes in
them are the ones production sees (ground rule: no invented API behavior)."""

import json
from pathlib import Path

import pytest

from producers.common.models import StateMessage
from producers.common.wx_parsing import (
    ceiling_ft,
    convert,
    flight_category,
    parse_observation,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# --- state vector envelope (PRD §5.1) ---------------------------------------

class TestStateEnvelope:
    def test_airborne_vector_maps_all_fields(self):
        payload = load("opensky_states_sample.json")
        vector = payload["states"][0]  # AAL874Q, airborne, fully populated
        message = StateMessage.from_state_vector(vector, poll_ts=1, api_ts=payload["time"])
        assert message.icao24 == "ab0361"
        assert message.callsign == "AAL874Q"
        assert message.lat == pytest.approx(41.2456)
        assert message.lon == pytest.approx(-73.71)
        assert message.baro_alt_m == pytest.approx(2423.16)
        assert message.on_ground is False
        assert message.velocity_ms == pytest.approx(160.38)

    def test_on_ground_null_altitude_passes_through(self):
        # Verified live: on-ground aircraft frequently report null baro_altitude.
        payload = load("opensky_states_sample.json")
        vector = next(v for v in payload["states"] if v[8])
        message = StateMessage.from_state_vector(vector, poll_ts=1, api_ts=payload["time"])
        assert message.on_ground is True
        assert message.baro_alt_m is None

    def test_empty_callsign_becomes_none(self):
        payload = load("opensky_states_sample.json")
        vector = next(v for v in payload["states"] if not (v[1] or "").strip())
        message = StateMessage.from_state_vector(vector, poll_ts=1, api_ts=payload["time"])
        assert message.callsign is None

    def test_null_position_survives_envelope(self):
        # Null lat/lon rows are dropped downstream at the bronze parse (PRD §7),
        # not by the envelope — mutate a recorded vector to the documented case.
        payload = load("opensky_states_sample.json")
        vector = list(payload["states"][0])
        vector[5] = None
        vector[6] = None
        message = StateMessage.from_state_vector(vector, poll_ts=1, api_ts=payload["time"])
        assert message.lat is None and message.lon is None


# --- unit conversion (PRD §5.2 gotcha) ---------------------------------------

class TestUnitConversion:
    def test_live_km_h_dash_1_form_accepted(self):
        # PRD §5.2 documents wmoUnit:km_h; live API returns wmoUnit:km_h-1
        # (recorded 2026-08-11). Both must convert as km/h.
        obs = load("nws_obs_kjfk.json")["properties"]
        assert obs["windSpeed"]["unitCode"] == "wmoUnit:km_h-1"
        assert convert(obs["windSpeed"], "kmh") == pytest.approx(18.504)

    def test_m_s_converts_to_kmh(self):
        assert convert({"value": 10, "unitCode": "wmoUnit:m_s-1"}, "kmh") == pytest.approx(36.0)

    def test_unknown_unit_yields_none_not_a_guess(self):
        assert convert({"value": 5, "unitCode": "wmoUnit:furlong"}, "kmh") is None

    def test_null_value_yields_none(self):
        obs = load("nws_obs_kjfk.json")["properties"]
        assert obs["windGust"]["value"] is None
        assert convert(obs["windGust"], "kmh") is None

    def test_cloud_base_m_converts_to_ft(self):
        assert convert({"value": 304.8, "unitCode": "wmoUnit:m"}, "ft") == pytest.approx(1000.0)


# --- ceiling derivation ------------------------------------------------------

class TestCeiling:
    def test_few_layers_set_no_ceiling(self):
        # Recorded KJFK sky: two FEW layers — FEW/SCT never constitute a ceiling.
        obs = load("nws_obs_kjfk.json")["properties"]
        assert all(l["amount"] == "FEW" for l in obs["cloudLayers"])
        assert ceiling_ft(obs["cloudLayers"]) is None

    def test_lowest_bkn_ovc_wins(self):
        layers = [
            {"amount": "SCT", "base": {"value": 152.4, "unitCode": "wmoUnit:m"}},
            {"amount": "BKN", "base": {"value": 304.8, "unitCode": "wmoUnit:m"}},
            {"amount": "OVC", "base": {"value": 609.6, "unitCode": "wmoUnit:m"}},
        ]
        assert ceiling_ft(layers) == pytest.approx(1000.0)

    def test_empty_layers_none(self):
        assert ceiling_ft([]) is None
        assert ceiling_ft(None) is None


# --- flight category boundaries (PRD §5.2 table) -----------------------------

SM = 1609.344  # meters per statute mile

class TestFlightCategory:
    @pytest.mark.parametrize(
        ("ceiling", "vis_m", "expected"),
        [
            (499, 10 * SM, "LIFR"),   # ceiling < 500
            (10_000, 0.9 * SM, "LIFR"),  # vis < 1 SM
            (500, 10 * SM, "IFR"),    # 500-999
            (999, 10 * SM, "IFR"),
            (10_000, 1 * SM, "IFR"),  # 1 <= vis < 3
            (10_000, 2.9 * SM, "IFR"),
            (1000, 10 * SM, "MVFR"),  # 1,000-3,000 inclusive
            (3000, 10 * SM, "MVFR"),
            (10_000, 3 * SM, "MVFR"),  # 3-5 SM inclusive
            (10_000, 5 * SM, "MVFR"),
            (3001, 5.1 * SM, "VFR"),  # strictly above both
            (None, 10 * SM, "VFR"),   # unlimited ceiling
        ],
    )
    def test_boundaries(self, ceiling, vis_m, expected):
        assert flight_category(ceiling, vis_m) == expected

    def test_worst_of_two_wins(self):
        assert flight_category(400, 10 * SM) == "LIFR"
        assert flight_category(5000, 0.5 * SM) == "LIFR"

    def test_no_data_is_none_not_vfr(self):
        assert flight_category(None, None) is None


# --- full observation parse over the recorded response -----------------------

class TestParseObservation:
    def test_recorded_kjfk_observation(self):
        obs = load("nws_obs_kjfk.json")["properties"]
        message = parse_observation("KJFK", obs)
        assert message.station == "KJFK"
        assert message.obs_ts == obs["timestamp"]
        assert message.wind_speed_kmh == pytest.approx(18.504)  # km/h, NOT halved/doubled
        assert message.visibility_m == pytest.approx(16093.44)
        assert message.ceiling_ft is None  # FEW-only sky
        assert message.flight_category == "VFR"  # vis 10 SM, no ceiling
        assert message.raw == obs
