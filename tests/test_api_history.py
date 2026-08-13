"""Public-API history query layer over gold_airport_day_delay (spec §3.2)."""

import pytest

from api import queries_history as h


class TestValidation:
    def test_year_range_rejected(self):
        with pytest.raises(ValueError):
            h.staircase_sql(from_year=1900, to_year=2020)
        with pytest.raises(ValueError):
            h.staircase_sql(from_year=2020, to_year=1990)  # inverted

    def test_worst_limit_capped(self):
        with pytest.raises(ValueError):
            h.worst_sql(limit=51, metric="wx_cancelled")

    def test_worst_bad_metric(self):
        with pytest.raises(ValueError):
            h.worst_sql(limit=10, metric="delay_total")

    def test_monthly_bad_category(self):
        with pytest.raises(ValueError):
            h.monthly_sql(category="SVFR", airport=None)

    def test_monthly_bad_airport(self):
        with pytest.raises(ValueError):
            h.monthly_sql(category=None, airport="KJFK")  # BTS codes here, not ICAO


class TestSqlShape:
    def test_staircase_parameterized(self):
        sql, params = h.staircase_sql(from_year=1987, to_year=2026)
        assert "gold_airport_day_delay" in sql
        assert params == {"from_y": 1987, "to_y": 2026}
        assert "%(from_y)s" in sql and "%(to_y)s" in sql

    def test_monthly_starts_2000(self):
        # Source coverage is continuous from 2000 (delay-history dashboard rule);
        # earlier decades would render a misleading gap.
        sql, _ = h.monthly_sql(category=None, airport=None)
        assert ">= 2000" in sql

    def test_causes_converts_to_thousand_hours(self):
        sql, _ = h.causes_sql()
        assert "60000" in sql  # minutes -> thousand hours

    def test_worst_orders_by_metric(self):
        sql_c, _ = h.worst_sql(limit=10, metric="wx_cancelled")
        sql_d, _ = h.worst_sql(limit=10, metric="wx_delay")
        assert "cancelled_weather desc" in sql_c
        assert "weather_delay_min_total desc" in sql_d
