-- SkyNYC serving-layer schema — PRD §8
--
-- TRAP (Manual 05): files in /docker-entrypoint-initdb.d run ONLY on an empty
-- pgdata volume, i.e. the very first boot. Editing this file later and
-- restarting does nothing. Post-first-boot changes go in new numbered files
-- (002_*.sql, ...) applied by hand:
--   docker compose exec -T postgres psql -U skynyc -d skynyc < db/init/002_change.sql

CREATE TABLE live_states (
  icao24 text PRIMARY KEY, callsign text, latitude double precision, longitude double precision,
  baro_alt_m real, on_ground boolean, velocity_ms real, track_deg real, vrate_ms real,
  category smallint, updated_at timestamptz NOT NULL);

CREATE TABLE flight_events (
  event_id text PRIMARY KEY, icao24 text NOT NULL, callsign text,
  event_type text NOT NULL CHECK (event_type IN ('arrival','holding','go_around')),
  airport text NOT NULL CHECK (airport IN ('KJFK','KLGA','KEWR')),
  event_ts timestamptz NOT NULL, duration_s int, details jsonb, detected_at timestamptz DEFAULT now());
CREATE INDEX idx_flight_events_airport_ts ON flight_events (airport, event_ts);

CREATE TABLE weather_obs (
  station text, obs_ts timestamptz, temp_c real, wind_speed_kmh real, wind_gust_kmh real,
  wind_dir_deg real, visibility_m real, ceiling_ft real, precip_last_hr_mm real,
  text_desc text, flight_category text, raw jsonb, PRIMARY KEY (station, obs_ts));

CREATE TABLE weather_alerts (
  alert_id text, airport text, event text, severity text,
  effective_ts timestamptz, ends_ts timestamptz, raw jsonb, PRIMARY KEY (alert_id, airport));

CREATE TABLE arrivals_ground_truth (
  icao24 text, airport text, callsign text, est_arrival_ts timestamptz,
  first_seen timestamptz, last_seen timestamptz, PRIMARY KEY (icao24, airport, est_arrival_ts));
