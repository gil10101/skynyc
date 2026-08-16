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

-- Coverage proxy: hours of the day in which the detection stream produced an
-- arrival at ANY of the three fields. The proxy's job is "was the stream
-- watching", and per-airport counting answered a different question — "was
-- this airport landing planes" — which docked LGA an hour of coverage every
-- quiet overnight (its 01:00–06:00 ET lull) and withheld recall on honest
-- days. JFK and EWR land around the clock, so a stream-alive hour is always
-- visible somewhere; a true outage silences all three fields at once and
-- still withholds. A quiet-hour miss at one airport now counts against
-- recall only when ground truth actually shows arrivals there — which is a
-- detector failure, exactly what recall measures.
detector_coverage as (
    select
        (event_ts at time zone 'UTC')::date as quality_date,
        count(distinct date_trunc('hour', event_ts)) as observed_hours
    from {{ ref('stg_flight_events') }}
    where event_type = 'arrival'
    group by 1
)

select
    coalesce(d.quality_date, g.quality_date) as quality_date,
    coalesce(d.airport, g.airport) as airport,
    coalesce(d.arrivals_detected, 0) as arrivals_detected,
    coalesce(g.arrivals_gt, 0) as arrivals_gt,
    coalesce(d.detected_matched, 0) as detected_matched,
    coalesce(g.gt_matched, 0) as gt_matched,
    coalesce(c.observed_hours, 0) as observed_hours,
    -- Ground truth arrives D-1 (PRD §5.1) and its upstream aggregation can lag
    -- further: a day may come back absent, or present but nearly empty. Neither
    -- is scoreable — 0 or near-0 matches against a missing reference is
    -- "unscored", not 0% precision. Completeness proxy: the detector's own
    -- count is an independent same-order estimate of the day's true arrivals,
    -- so ground truth at less than half the detected count means the reference
    -- day is incomplete, not that the detector over-fired. Score only days
    -- whose ground truth has landed at plausible volume.
    case when coalesce(g.arrivals_gt, 0) >= 0.5 * coalesce(d.arrivals_detected, 0)
          and coalesce(g.arrivals_gt, 0) > 0
         then round(d.detected_matched::numeric / nullif(d.arrivals_detected, 0), 4)
    end as "precision",
    -- Recall additionally requires the detector to have observed essentially
    -- the whole day (>= 20 of 24 hours): full-day ground truth scored against
    -- a partial detection day reports absence as misses. The half-of-detected
    -- floor applies here too — recall against a sliver of ground truth is a
    -- coin flip, not a score.
    case when coalesce(g.arrivals_gt, 0) >= 0.5 * coalesce(d.arrivals_detected, 0)
          and coalesce(g.arrivals_gt, 0) > 0
          and coalesce(c.observed_hours, 0) >= 20
         then round(g.gt_matched::numeric / nullif(g.arrivals_gt, 0), 4)
    end as recall
from detected_daily d
full outer join gt_daily g using (airport, quality_date)
left join detector_coverage c using (quality_date)
