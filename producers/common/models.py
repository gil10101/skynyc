"""Shared message models — the single contract between producers, the Spark
schema (M2), and the weather consumer.

Envelope shapes are fixed by PRD §5.1/§5.2. Field names here ARE the Kafka
message keys' payload schema; change them only with a spec change.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# /states/all state-vector indices used (PRD §5.1). The response is positional;
# indexing anywhere else in the codebase is a bug — parse here, once.
IDX_ICAO24 = 0
IDX_CALLSIGN = 1
IDX_LON = 5
IDX_LAT = 6
IDX_BARO_ALT = 7
IDX_ON_GROUND = 8
IDX_VELOCITY = 9
IDX_TRUE_TRACK = 10
IDX_VERTICAL_RATE = 11
IDX_CATEGORY = 17  # present only with extended=1


class StateMessage(BaseModel):
    """One state vector on `adsb.states.raw`, key = icao24 (PRD §5.1 envelope).

    lat/lon/baro_alt_m/velocity_ms/track_deg/vrate_ms are nullable in the source
    and stay nullable here — null-position rows are dropped downstream at the
    bronze parse (PRD §7 Q1), not by the producer. The producer publishes the
    raw truth and counts nulls as a metric.
    """

    model_config = ConfigDict(extra="forbid")

    poll_ts: int
    api_ts: int
    icao24: str
    callsign: str | None = None
    lon: float | None = None
    lat: float | None = None
    baro_alt_m: float | None = None
    on_ground: bool
    velocity_ms: float | None = None
    track_deg: float | None = None
    vrate_ms: float | None = None
    category: int | None = None

    @classmethod
    def from_state_vector(cls, vector: list[Any], poll_ts: int, api_ts: int) -> "StateMessage":
        callsign = vector[IDX_CALLSIGN]
        callsign = callsign.strip() if isinstance(callsign, str) else None
        return cls(
            poll_ts=poll_ts,
            api_ts=api_ts,
            icao24=vector[IDX_ICAO24],
            callsign=callsign or None,
            lon=vector[IDX_LON],
            lat=vector[IDX_LAT],
            baro_alt_m=vector[IDX_BARO_ALT],
            on_ground=bool(vector[IDX_ON_GROUND]),
            velocity_ms=vector[IDX_VELOCITY],
            track_deg=vector[IDX_TRUE_TRACK],
            vrate_ms=vector[IDX_VERTICAL_RATE],
            category=vector[IDX_CATEGORY] if len(vector) > IDX_CATEGORY else None,
        )


class WeatherObsMessage(BaseModel):
    """One observation on `wx.observations`, key = station. Columns mirror the
    weather_obs table (PRD §8); units are converted at parse from unitCode and
    baked into the field names."""

    model_config = ConfigDict(extra="forbid")

    station: str
    obs_ts: str  # ISO-8601 with offset, straight from properties.timestamp
    temp_c: float | None = None
    wind_speed_kmh: float | None = None
    wind_gust_kmh: float | None = None
    wind_dir_deg: float | None = None
    visibility_m: float | None = None
    ceiling_ft: float | None = None
    precip_last_hr_mm: float | None = None
    text_desc: str | None = None
    flight_category: str | None = None
    raw: dict[str, Any]


class WeatherAlertMessage(BaseModel):
    """One active alert on `wx.alerts`, key = airport. PK downstream is
    (alert_id, airport) — the same alert can cover multiple airports."""

    model_config = ConfigDict(extra="forbid")

    alert_id: str
    airport: str
    event: str | None = None
    severity: str | None = None
    effective_ts: str | None = None
    ends_ts: str | None = None
    raw: dict[str, Any]
