"""Public-API live query layer (public-dashboard spec §3.2).

Builders return (sql, params) and never touch the database, so validation and
SQL shape are tested without a running Postgres — the executors are one-line
wrappers exercised by the deploy smoke (scripts/smoke_api.py).
"""

import pytest

from api import queries_live as q


class TestValidation:
    def test_hours_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            q.arrivals_sql(hours=0, airports=["KJFK"])
        with pytest.raises(ValueError):
            q.arrivals_sql(hours=169, airports=["KJFK"])

    def test_unknown_airport_rejected(self):
        with pytest.raises(ValueError):
            q.arrivals_sql(hours=24, airports=["KLAX"])

    def test_limit_capped(self):
        with pytest.raises(ValueError):
            q.events_sql(event_type=None, airport=None, limit=201)

    def test_bad_event_type_rejected(self):
        with pytest.raises(ValueError):
            q.events_sql(event_type="departure", airport=None, limit=10)

    def test_scatter_days_bounded(self):
        with pytest.raises(ValueError):
            q.scatter_sql(days=91)


class TestSqlShape:
    def test_arrivals_sql_parameterized(self):
        sql, params = q.arrivals_sql(hours=24, airports=["KJFK", "KLGA"])
        assert "%(hours)s" in sql and params["hours"] == 24
        assert params["airports"] == ["KJFK", "KLGA"]
        assert "flight_events" in sql and "900" in sql  # 15-min buckets

    def test_arrivals_defaults_to_all_airports(self):
        _, params = q.arrivals_sql(hours=3)
        assert params["airports"] == list(q.AIRPORTS)

    def test_snapshot_positions_age_bounded(self):
        sql, _ = q.positions_sql()
        assert "interval '5 min'" in sql
        assert "latitude is not null" in sql

    def test_events_sql_null_filters_pass_through(self):
        sql, params = q.events_sql(event_type=None, airport=None, limit=50)
        assert params["event_type"] is None and params["airport"] is None
        assert "order by event_ts desc" in sql

    def test_quality_passes_nulls_through(self):
        # Unscored days must reach the client as nulls, never zeros — the SQL
        # must not coalesce precision/recall.
        sql, _ = q.quality_sql(days=30)
        assert "coalesce(\"precision\"" not in sql.lower()
        assert "mart_detection_quality" in sql
