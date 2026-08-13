"""Route layer: param passthrough, 400s, CORS allowlist, GET-only, meta status,
sampler behavior. Executors are monkeypatched — no database."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main as m

SNAP = {"generated_at": "t", "positions": [{"icao24": "abc"}], "conditions": [],
        "alerts": [], "freshness_s": 12}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(m.live, "live_snapshot", lambda: dict(SNAP))
    monkeypatch.setattr(m.live, "arrivals",
                        lambda hours, airports=None: {"generated_at": "t", "series": [],
                                                      "hours": hours, "airports": airports})
    monkeypatch.setattr(m.live, "airborne",
                        lambda hours, airports=None: {"generated_at": "t", "series": []})
    monkeypatch.setattr(m.live, "recent_events",
                        lambda event_type, airport, limit: {"generated_at": "t", "events": []})
    monkeypatch.setattr(m.live, "wind", lambda hours: {"generated_at": "t", "series": []})
    monkeypatch.setattr(m.live, "scatter", lambda days: {"generated_at": "t", "points": []})
    monkeypatch.setattr(m.live, "quality", lambda days: {"generated_at": "t", "days": []})
    monkeypatch.setattr(m.hist, "staircase",
                        lambda from_year, to_year: {"generated_at": "t", "cells": []})
    monkeypatch.setattr(m.hist, "worst",
                        lambda limit, metric: {"generated_at": "t", "days": []})
    return TestClient(m.app)


class TestRoutes:
    def test_snapshot_ok(self, client):
        r = client.get("/v1/snapshot")
        assert r.status_code == 200
        assert r.json()["freshness_s"] == 12
        assert "max-age=5" in r.headers["cache-control"]

    def test_arrivals_param_passthrough(self, client):
        r = client.get("/v1/arrivals?hours=12&airport=KJFK,KLGA")
        assert r.status_code == 200
        assert r.json()["hours"] == 12
        assert r.json()["airports"] == ["KJFK", "KLGA"]

    def test_arrivals_bad_hours_400(self, client, monkeypatch):
        def boom(hours, airports=None):
            raise ValueError("hours must be in [1,168]")
        monkeypatch.setattr(m.live, "arrivals", boom)
        r = client.get("/v1/arrivals?hours=999")
        assert r.status_code == 400
        assert "hours" in r.json()["detail"]

    def test_get_only(self, client):
        assert client.post("/v1/snapshot").status_code == 405

    def test_history_staircase(self, client):
        assert client.get("/v1/history/staircase?from_year=2000&to_year=2020").status_code == 200

    def test_meta_fresh(self, client):
        r = client.get("/v1/meta")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "fresh" and body["aircraft"] == 1

    def test_meta_offline(self, client, monkeypatch):
        stale = dict(SNAP, freshness_s=100000)
        monkeypatch.setattr(m.live, "live_snapshot", lambda: stale)
        assert client.get("/v1/meta").json()["status"] == "offline"

    def test_docs_disabled(self, client):
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


class TestCors:
    def test_allowed_origin_echoed(self, client):
        r = client.get("/v1/snapshot", headers={"Origin": "https://sky.gillu.me"})
        assert r.headers.get("access-control-allow-origin") == "https://sky.gillu.me"

    def test_disallowed_origin_absent(self, client):
        r = client.get("/v1/snapshot", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


class TestSampler:
    def test_tick_sets_state(self, monkeypatch):
        monkeypatch.setattr(m.live, "live_snapshot", lambda: dict(SNAP))
        asyncio.run(m.sampler_tick())
        assert m.app.state.latest_snapshot["freshness_s"] == 12

    def test_tick_failure_keeps_last_good(self, monkeypatch):
        m.app.state.latest_snapshot = {"generated_at": "prev"}
        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(m.live, "live_snapshot", boom)
        asyncio.run(m.sampler_tick())  # must not raise
        assert m.app.state.latest_snapshot["generated_at"] == "prev"


class TestSerialization:
    def test_db_types_serialize(self, client, monkeypatch):
        # Rows out of psycopg carry datetime/date/Decimal — a 500 here is the
        # bug the first droplet smoke caught.
        from datetime import datetime, timezone
        from decimal import Decimal
        monkeypatch.setattr(
            m.live, "wind",
            lambda hours: {"generated_at": "t", "series": [{
                "obs_ts": datetime(2026, 8, 13, tzinfo=timezone.utc),
                "wind_speed_kmh": Decimal("14.8"),
            }]},
        )
        r = client.get("/v1/wind?hours=3")
        assert r.status_code == 200
        row = r.json()["series"][0]
        assert row["obs_ts"].startswith("2026-08-13T")
        assert row["wind_speed_kmh"] == 14.8
