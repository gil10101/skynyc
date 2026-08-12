-- Grain: one row per airport per UTC hour.
--
-- The spine spans the detector stream's lifetime: from the hour of the first
-- detected event to the last fully elapsed hour. The current hour is excluded
-- because it is still filling and would read as a false traffic drop on every
-- chart built on top. Hours inside the window with zero events stay in the
-- spine on purpose — a quiet hour is data, and a dead-stream hour must show
-- as zeros, not vanish.
--
-- Must stay a view (dbt_project.yml): now() makes this non-deterministic, and
-- as a view it inlines into each mart's single CREATE TABLE AS, where now()
-- is transaction-stable — every reference sees the same end hour. Materialize
-- it as a table and the newest hour can straddle two builds.
with bounds as (
    select
        date_trunc('hour', min(event_ts)) as start_hour,
        date_trunc('hour', now()) as end_hour   -- exclusive
    from {{ ref('stg_flight_events') }}
),

hours as (
    select generate_series(
        start_hour, end_hour - interval '1 hour', interval '1 hour'
    ) as hour_ts
    from bounds
),

airports as (
    select unnest(array['KJFK', 'KLGA', 'KEWR']) as airport
)

select
    airports.airport,
    hours.hour_ts
from airports
cross join hours
