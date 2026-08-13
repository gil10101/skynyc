"""Historical queries over gold_airport_day_delay (spec §3.2).

Airports here are BTS codes (JFK/LGA/EWR), not ICAO — the gold mart's grain.
Delay-cause columns are null before June 2003 (unattributed, never zero); the
SQL passes those nulls through.
"""

from __future__ import annotations

from datetime import datetime, timezone

from api import db
from api.cache import ttl

BTS_AIRPORTS = ("JFK", "LGA", "EWR")
CATEGORIES = ("VFR", "MVFR", "IFR", "LIFR")
WORST_METRICS = ("wx_cancelled", "wx_delay")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_year(name: str, value: int) -> int:
    if not 1987 <= value <= 2100:
        raise ValueError(f"{name} must be in [1987,2100]")
    return value


# --- builders ----------------------------------------------------------------

def staircase_sql(from_year: int, to_year: int) -> tuple[str, dict]:
    _check_year("from_year", from_year)
    _check_year("to_year", to_year)
    if from_year > to_year:
        raise ValueError("from_year must not exceed to_year")
    return ("""
        select airport, worst_category, count(*) as days,
               round(avg(avg_arr_delay_min)::numeric, 1) as avg_delay_min
        from gold_airport_day_delay
        where worst_category is not null
          and extract(year from flight_date) between %(from_y)s and %(to_y)s
        group by 1, 2
    """, {"from_y": from_year, "to_y": to_year})


def causes_sql() -> tuple[str, dict]:
    # minutes -> thousand hours (60 * 1000); attribution exists from June 2003.
    return ("""
        select extract(year from flight_date)::int as year,
               round((sum(weather_delay_min_total) / 60000.0)::numeric, 1) as weather_khr,
               round((sum(nas_delay_min_total) / 60000.0)::numeric, 1) as nas_khr
        from gold_airport_day_delay
        group by 1
        having sum(weather_delay_min_total) is not null
        order by 1
    """, {})


def seasonality_sql() -> tuple[str, dict]:
    return ("""
        select airport, extract(month from flight_date)::int as month,
               sum(cancelled_weather) as wx_cancelled
        from gold_airport_day_delay
        group by 1, 2
        order by 1, 2
    """, {})


def worst_sql(limit: int, metric: str) -> tuple[str, dict]:
    if not 1 <= limit <= 50:
        raise ValueError("limit must be in [1,50]")
    if metric not in WORST_METRICS:
        raise ValueError(f"metric must be one of {WORST_METRICS}")
    order = "cancelled_weather desc" if metric == "wx_cancelled" else "weather_delay_min_total desc"
    return (f"""
        select flight_date, airport, worst_category, arrivals_scheduled,
               cancelled, cancelled_weather,
               round((weather_delay_min_total / 60.0)::numeric, 1) as wx_delay_hours,
               min_visibility_sm, min_ceiling_ft
        from gold_airport_day_delay
        where cancelled_weather is not null
        order by {order} nulls last
        limit %(limit)s
    """, {"limit": limit})


def monthly_sql(category: str | None, airport: str | None) -> tuple[str, dict]:
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    if airport is not None and airport not in BTS_AIRPORTS:
        raise ValueError(f"airport must be one of {BTS_AIRPORTS}")
    # Coverage is continuous from 2000 (source publishes nothing for 1990-1999);
    # rendering earlier would draw a misleading gap.
    return ("""
        select date_trunc('month', flight_date)::date as month, worst_category,
               round(avg(avg_arr_delay_min)::numeric, 1) as avg_delay_min
        from gold_airport_day_delay
        where worst_category is not null
          and extract(year from flight_date) >= 2000
          and (%(category)s::text is null or worst_category = %(category)s)
          and (%(airport)s::text is null or airport = %(airport)s)
        group by 1, 2
        order by 1
    """, {"category": category, "airport": airport})


# --- executors (cached) ------------------------------------------------------

@ttl(600)
def staircase(from_year: int, to_year: int) -> dict:
    sql, params = staircase_sql(from_year, to_year)
    return {"generated_at": _now(), "cells": db.rows(sql, params)}


@ttl(600)
def causes() -> dict:
    sql, params = causes_sql()
    return {"generated_at": _now(), "years": db.rows(sql, params)}


@ttl(600)
def seasonality() -> dict:
    sql, params = seasonality_sql()
    return {"generated_at": _now(), "months": db.rows(sql, params)}


@ttl(600)
def worst(limit: int, metric: str) -> dict:
    sql, params = worst_sql(limit, metric)
    return {"generated_at": _now(), "days": db.rows(sql, params)}


@ttl(600)
def monthly(category: str | None, airport: str | None) -> dict:
    sql, params = monthly_sql(category, airport)
    return {"generated_at": _now(), "months": db.rows(sql, params)}
