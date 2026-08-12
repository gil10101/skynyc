-- Historical lakehouse serving table (PRD §13 M4A, v1.4). Grain: one row per
-- (flight_date, airport) for JFK/LGA/EWR across the full BTS history. Loaded by
-- the gold_airport_day_delay_pg Dagster asset from the lake's gold CSV export;
-- every load is an upsert on the grain, so replays converge.
--
-- Delay-cause columns are NULL before June 2003 (the source only attributes
-- causes from then on) — NULL means unattributed, never zero.
--
-- Apply by hand (init scripts only run on an empty volume — Manual 05):
--   docker compose exec -T postgres psql -U skynyc -d skynyc \
--     < db/migrations/003_gold_airport_day_delay.sql

CREATE TABLE IF NOT EXISTS gold_airport_day_delay (
    flight_date              date             NOT NULL,
    airport                  text             NOT NULL,
    arrivals_scheduled       integer,
    cancelled                integer,
    cancelled_weather        integer,
    avg_arr_delay_min        double precision,
    p90_arr_delay_min        double precision,
    arrivals_delayed_15      integer,
    weather_delay_min_total  double precision,
    nas_delay_min_total      double precision,
    pct_obs_ifr_or_worse     double precision,
    min_visibility_sm        double precision,
    min_ceiling_ft           double precision,
    obs_count                integer,
    worst_category           text,
    PRIMARY KEY (flight_date, airport)
);

CREATE INDEX IF NOT EXISTS idx_gold_add_category
    ON gold_airport_day_delay (worst_category, airport);
