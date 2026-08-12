-- All analytical day/hour bucketing is UTC by definition (PRD §9). The models
-- pin UTC explicitly with AT TIME ZONE, so this is defense-in-depth for ad-hoc
-- sessions and future code: a session created with a non-UTC TimeZone would
-- otherwise cast timestamptz to different calendar days than the pipeline.
-- Runs automatically only on an empty volume (Manual 05) — apply by hand on
-- an existing database.
ALTER DATABASE skynyc SET timezone = 'UTC';
