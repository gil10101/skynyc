-- 002 — dedicated schema for Dagster's run/event storage (M3).
--
-- Applied BY HAND on existing databases (the init directory only runs on an
-- empty volume — Manual 05):
--   docker compose exec -T postgres psql -U skynyc -d skynyc < db/init/002_dagster_schema.sql
-- Idempotent: safe to re-run.
CREATE SCHEMA IF NOT EXISTS dagster;
