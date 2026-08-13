"""Live-window queries for the public API (spec §3.2).

Every builder returns (sql, params); executors run them through the read-only
pool and stamp generated_at. Params are validated here so routes stay thin.
Nulls pass through untouched — an unscored day or a missing measurement must
reach the client as null, never zero (ground rule 4).
"""

from __future__ import annotations

from datetime import datetime, timezone

from api import db
from api.cache import ttl

AIRPORTS = ("KJFK", "KLGA", "KEWR")
EVENT_TYPES = ("arrival", "holding", "go_around")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_airports(airports: list[str] | None) -> list[str]:
    aps = list(airports) if airports else list(AIRPORTS)
    if not set(aps) <= set(AIRPORTS):
        raise ValueError(f"airports must be within {AIRPORTS}")
    return aps


def _check_range(name: str, value: int, lo: int, hi: int) -> int:
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be in [{lo},{hi}]")
    return value


# --- builders ----------------------------------------------------------------

def positions_sql() -> tuple[str, dict]:
    return ("""
        select icao24, callsign, latitude as lat, longitude as lon,
               baro_alt_m as alt_m, on_ground, velocity_ms as vel_ms,
               track_deg, vrate_ms, category,
               extract(epoch from now() - updated_at)::int as age_s
        from live_states
        where updated_at > now() - interval '5 min'
          and latitude is not null and longitude is not null
    """, {})


def conditions_sql() -> tuple[str, dict]:
    return ("""
        select distinct on (station) station, obs_ts, temp_c, wind_speed_kmh,
               wind_gust_kmh, wind_dir_deg, visibility_m, ceiling_ft,
               flight_category, text_desc
        from weather_obs
        order by station, obs_ts desc
    """, {})


def alerts_sql() -> tuple[str, dict]:
    return ("""
        select alert_id, airport, event, severity, effective_ts, ends_ts
        from weather_alerts
        where effective_ts <= now()
          and coalesce(ends_ts, 'infinity'::timestamptz) > now()
        order by airport, effective_ts desc
    """, {})


def freshness_sql() -> tuple[str, dict]:
    return (
        "select extract(epoch from now() - max(updated_at))::int as freshness_s from live_states",
        {},
    )


def arrivals_sql(hours: int, airports: list[str] | None = None) -> tuple[str, dict]:
    _check_range("hours", hours, 1, 168)
    return ("""
        select airport,
               to_timestamp(floor(extract(epoch from event_ts) / 900) * 900) as bucket_ts,
               count(*) as arrivals
        from flight_events
        where event_type = 'arrival'
          and airport = any(%(airports)s)
          and event_ts > now() - make_interval(hours => %(hours)s)
        group by 1, 2
        order by 2
    """, {"hours": hours, "airports": _check_airports(airports)})


def airborne_sql(hours: int, airports: list[str] | None = None) -> tuple[str, dict]:
    _check_range("hours", hours, 1, 168)
    return ("""
        select airport, date_trunc('hour', event_ts) as bucket_ts,
               round(coalesce(sum(duration_s) filter (where event_type = 'holding'), 0)
                     / 60.0, 1) as holding_min,
               count(*) filter (where event_type = 'go_around') as go_arounds
        from flight_events
        where event_type in ('holding', 'go_around')
          and airport = any(%(airports)s)
          and event_ts > now() - make_interval(hours => %(hours)s)
        group by 1, 2
        order by 2
    """, {"hours": hours, "airports": _check_airports(airports)})


def events_sql(event_type: str | None, airport: str | None, limit: int) -> tuple[str, dict]:
    _check_range("limit", limit, 1, 200)
    if event_type is not None and event_type not in EVENT_TYPES:
        raise ValueError(f"event_type must be one of {EVENT_TYPES}")
    if airport is not None:
        _check_airports([airport])
    return ("""
        select event_id, icao24, callsign, event_type, airport, event_ts,
               duration_s, details
        from flight_events
        where (%(event_type)s::text is null or event_type = %(event_type)s)
          and (%(airport)s::text is null or airport = %(airport)s)
        order by event_ts desc
        limit %(limit)s
    """, {"event_type": event_type, "airport": airport, "limit": limit})


def wind_sql(hours: int) -> tuple[str, dict]:
    _check_range("hours", hours, 1, 168)
    return ("""
        select station, obs_ts, wind_speed_kmh, wind_gust_kmh
        from weather_obs
        where obs_ts > now() - make_interval(hours => %(hours)s)
        order by obs_ts
    """, {"hours": hours})


def scatter_sql(days: int) -> tuple[str, dict]:
    _check_range("days", days, 1, 90)
    return ("""
        select airport, hour_ts, arrivals_detected,
               coalesce(wind_gust_kmh, wind_speed_kmh) as effective_wind_kmh,
               flight_category
        from analytics.fct_airport_hourly
        where hour_ts > now() - make_interval(days => %(days)s)
          and coalesce(wind_gust_kmh, wind_speed_kmh) is not null
    """, {"days": days})


def quality_sql(days: int) -> tuple[str, dict]:
    _check_range("days", days, 1, 90)
    return ("""
        select quality_date, airport, arrivals_detected, arrivals_gt,
               observed_hours, "precision", recall
        from analytics.mart_detection_quality
        where quality_date > current_date - %(days)s
        order by quality_date, airport
    """, {"days": days})


# --- executors (cached) ------------------------------------------------------

@ttl(30)
def arrivals(hours: int, airports: tuple[str, ...] | None = None) -> dict:
    sql, params = arrivals_sql(hours, list(airports) if airports else None)
    return {"generated_at": _now(), "series": db.rows(sql, params)}


@ttl(30)
def airborne(hours: int, airports: tuple[str, ...] | None = None) -> dict:
    sql, params = airborne_sql(hours, list(airports) if airports else None)
    return {"generated_at": _now(), "series": db.rows(sql, params)}


@ttl(30)
def recent_events(event_type: str | None, airport: str | None, limit: int) -> dict:
    sql, params = events_sql(event_type, airport, limit)
    return {"generated_at": _now(), "events": db.rows(sql, params)}


@ttl(30)
def wind(hours: int) -> dict:
    sql, params = wind_sql(hours)
    return {"generated_at": _now(), "series": db.rows(sql, params)}


@ttl(300)
def scatter(days: int) -> dict:
    sql, params = scatter_sql(days)
    return {"generated_at": _now(), "points": db.rows(sql, params)}


@ttl(300)
def quality(days: int) -> dict:
    sql, params = quality_sql(days)
    return {"generated_at": _now(), "days": db.rows(sql, params)}


@ttl(5)
def live_snapshot() -> dict:
    out: dict = {"generated_at": _now()}
    for key, (sql, params) in {
        "positions": positions_sql(),
        "conditions": conditions_sql(),
        "alerts": alerts_sql(),
    }.items():
        out[key] = db.rows(sql, params)
    out["freshness_s"] = db.rows(*freshness_sql())[0]["freshness_s"]
    return out
