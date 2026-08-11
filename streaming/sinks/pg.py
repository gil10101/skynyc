"""Postgres sinks for Spark foreachBatch (PRD §7 Q2).

Batch sizes here are tiny — one row per aircraft currently in the box (~250) —
so a collect + executemany upsert is the honest implementation. All writes are
idempotent upserts (ground rule 7): re-processing a micro-batch after a crash
lands on the same rows.
"""

from __future__ import annotations

import os

import psycopg

UPSERT_LIVE_STATE = """
INSERT INTO live_states (icao24, callsign, latitude, longitude, baro_alt_m, on_ground,
                         velocity_ms, track_deg, vrate_ms, category, updated_at)
VALUES (%(icao24)s, %(callsign)s, %(latitude)s, %(longitude)s, %(baro_alt_m)s, %(on_ground)s,
        %(velocity_ms)s, %(track_deg)s, %(vrate_ms)s, %(category)s, %(updated_at)s)
ON CONFLICT (icao24) DO UPDATE SET
  callsign = EXCLUDED.callsign, latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
  baro_alt_m = EXCLUDED.baro_alt_m, on_ground = EXCLUDED.on_ground,
  velocity_ms = EXCLUDED.velocity_ms, track_deg = EXCLUDED.track_deg,
  vrate_ms = EXCLUDED.vrate_ms, category = EXCLUDED.category, updated_at = EXCLUDED.updated_at
WHERE EXCLUDED.updated_at > live_states.updated_at
"""
# The WHERE guard makes out-of-order micro-batches harmless: an older position
# never overwrites a newer one, so crash-replays cannot move aircraft backwards.


def upsert_live_states(rows: list[dict]) -> None:
    """Upsert one micro-batch of latest-per-aircraft positions."""
    if not rows:
        return
    with psycopg.connect(os.environ["PG_DSN"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(UPSERT_LIVE_STATE, rows)
        connection.commit()


UPSERT_FLIGHT_EVENT = """
INSERT INTO flight_events (event_id, icao24, callsign, event_type, airport,
                           event_ts, duration_s, details)
VALUES (%(event_id)s, %(icao24)s, %(callsign)s, %(event_type)s, %(airport)s,
        to_timestamp(%(event_ts)s), %(duration_s)s, %(details)s)
ON CONFLICT (event_id) DO UPDATE SET
  duration_s = EXCLUDED.duration_s, details = EXCLUDED.details
"""
# Deterministic event_id (ground rule 7): a replayed window UPDATEs the same
# rows instead of duplicating them. duration/details may legitimately refine
# on replay (e.g. a holding close seen with more context); identity never does.


def upsert_flight_events(rows: list[dict]) -> None:
    """Idempotent insert of one micro-batch of detected events."""
    if not rows:
        return
    for row in rows:
        row["details"] = psycopg.types.json.Json(row.get("details") or {})
    with psycopg.connect(os.environ["PG_DSN"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(UPSERT_FLIGHT_EVENT, rows)
        connection.commit()
