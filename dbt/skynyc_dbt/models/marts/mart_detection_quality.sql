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
        -- UTC day pinned explicitly: a bare cast follows the session
        -- TimeZone and would shift day buckets in a non-UTC session.
        (d.event_ts at time zone 'UTC')::date as quality_date,
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
        (g.est_arrival_ts at time zone 'UTC')::date as quality_date,
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
),

-- Coverage proxy: hours of the day in which the detector saw at least one
-- arrival at that airport. These fields land arrivals around the clock, so a
-- silent hour at day scale means the stream was not running, not that the sky
-- was empty. Days the detector only partially observed cannot publish recall
-- honestly — the misses are absence, not detector failure.
detector_coverage as (
    select
        airport,
        (event_ts at time zone 'UTC')::date as quality_date,
        count(distinct date_trunc('hour', event_ts)) as observed_hours
    from {{ ref('stg_flight_events') }}
    where event_type = 'arrival'
    group by 1, 2
)

select
    coalesce(d.quality_date, g.quality_date) as quality_date,
    coalesce(d.airport, g.airport) as airport,
    coalesce(d.arrivals_detected, 0) as arrivals_detected,
    coalesce(g.arrivals_gt, 0) as arrivals_gt,
    coalesce(d.detected_matched, 0) as detected_matched,
    coalesce(g.gt_matched, 0) as gt_matched,
    coalesce(c.observed_hours, 0) as observed_hours,
    -- Ground truth arrives D-1 (PRD §5.1): the most recent detected day has no
    -- GT rows yet, and 0 matches against an absent reference is "unscored",
    -- not 0% precision. Score only days whose GT pull has landed.
    case when coalesce(g.arrivals_gt, 0) > 0
         then round(d.detected_matched::numeric / nullif(d.arrivals_detected, 0), 4)
    end as "precision",
    -- Recall additionally requires the detector to have observed essentially
    -- the whole day (>= 20 of 24 hours): full-day ground truth scored against
    -- a partial detection day reports absence as misses. Precision needs no
    -- such gate — every detected event can be checked regardless of coverage.
    case when coalesce(g.arrivals_gt, 0) > 0 and coalesce(c.observed_hours, 0) >= 20
         then round(g.gt_matched::numeric / nullif(g.arrivals_gt, 0), 4)
    end as recall
from detected_daily d
full outer join gt_daily g using (airport, quality_date)
left join detector_coverage c using (airport, quality_date)
