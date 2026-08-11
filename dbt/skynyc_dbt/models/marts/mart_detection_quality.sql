-- Grain: one row per UTC day per airport (PRD §9 validation logic).
--
-- A detected arrival matches ground truth when same icao24, same airport,
-- |Δt| <= 10 minutes. Precision = matched detected / all detected;
-- recall = matched GT / all GT. Match tests run in both directions with
-- EXISTS — at a few hundred arrivals/day per airport, 1:1 assignment would
-- move the score by noise, not signal.

with detected as (
    select event_id, icao24, airport, event_ts
    from {{ ref('stg_flight_events') }}
    where event_type = 'arrival'
),

ground_truth as (
    select icao24, airport, est_arrival_ts
    from {{ ref('stg_arrivals_gt') }}
),

detected_scored as (
    select
        d.airport,
        date_trunc('day', d.event_ts)::date as quality_date,
        exists (
            select 1 from ground_truth g
            where g.icao24 = d.icao24
              and g.airport = d.airport
              and abs(extract(epoch from g.est_arrival_ts - d.event_ts)) <= 600
        ) as matched
    from detected d
),

gt_scored as (
    select
        g.airport,
        date_trunc('day', g.est_arrival_ts)::date as quality_date,
        exists (
            select 1 from detected d
            where d.icao24 = g.icao24
              and d.airport = g.airport
              and abs(extract(epoch from g.est_arrival_ts - d.event_ts)) <= 600
        ) as matched
    from ground_truth g
),

detected_daily as (
    select airport, quality_date,
           count(*) as arrivals_detected,
           count(*) filter (where matched) as detected_matched
    from detected_scored group by 1, 2
),

gt_daily as (
    select airport, quality_date,
           count(*) as arrivals_gt,
           count(*) filter (where matched) as gt_matched
    from gt_scored group by 1, 2
)

select
    coalesce(d.quality_date, g.quality_date) as quality_date,
    coalesce(d.airport, g.airport) as airport,
    coalesce(d.arrivals_detected, 0) as arrivals_detected,
    coalesce(g.arrivals_gt, 0) as arrivals_gt,
    coalesce(d.detected_matched, 0) as detected_matched,
    coalesce(g.gt_matched, 0) as gt_matched,
    round(d.detected_matched::numeric / nullif(d.arrivals_detected, 0), 4) as "precision",
    round(g.gt_matched::numeric / nullif(g.arrivals_gt, 0), 4) as recall
from detected_daily d
full outer join gt_daily g using (airport, quality_date)
