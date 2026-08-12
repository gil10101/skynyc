-- Grain: one row per airport per UTC hour (the spine).
--
-- The operational heartbeat: what the detectors saw, what ground truth says,
-- and what the weather was, one row per airport-hour. Zero-activity hours are
-- real rows (spine semantics), so rate math downstream divides by observed
-- hours, not by hours that happened to have events.
with spine as (
    select airport, hour_ts from {{ ref('int_hours_spine') }}
),

events_hourly as (
    select
        airport,
        date_trunc('hour', event_ts) as hour_ts,
        count(*) filter (where event_type = 'arrival') as arrivals_detected,
        count(*) filter (where event_type = 'holding') as holding_events,
        round(coalesce(
            sum(duration_s) filter (where event_type = 'holding'), 0
        ) / 60.0, 1) as holding_minutes,
        count(*) filter (where event_type = 'go_around') as go_arounds
    from {{ ref('stg_flight_events') }}
    group by 1, 2
),

gt_hourly as (
    select
        airport,
        date_trunc('hour', est_arrival_ts) as hour_ts,
        count(*) as arrivals_gt
    from {{ ref('stg_arrivals_gt') }}
    group by 1, 2
),

-- Ground truth arrives D-1 (PRD §5.1): a day absent from GT is unscored, not
-- zero-traffic. Same rule as mart_detection_quality's precision guard.
-- Day boundaries are UTC by definition here, so the cast pins the zone: a
-- bare ::date on timestamptz follows the session TimeZone, and a non-UTC
-- session would smear the coverage boundary across real UTC days.
gt_days as (
    select distinct (est_arrival_ts at time zone 'UTC')::date as gt_date
    from {{ ref('stg_arrivals_gt') }}
)

select
    spine.airport,
    spine.hour_ts,
    coalesce(events_hourly.arrivals_detected, 0) as arrivals_detected,
    case when gt_days.gt_date is not null
         then coalesce(gt_hourly.arrivals_gt, 0)
    end as arrivals_gt,
    coalesce(events_hourly.holding_events, 0) as holding_events,
    coalesce(events_hourly.holding_minutes, 0) as holding_minutes,
    coalesce(events_hourly.go_arounds, 0) as go_arounds,
    weather.wx_obs_ts,
    weather.flight_category,
    weather.wind_speed_kmh,
    weather.wind_gust_kmh,
    weather.visibility_m,
    weather.ceiling_ft,
    weather.precip_last_hr_mm,
    weather.active_alert_types
from spine
left join events_hourly using (airport, hour_ts)
left join gt_hourly using (airport, hour_ts)
left join gt_days on (spine.hour_ts at time zone 'UTC')::date = gt_days.gt_date
left join {{ ref('int_weather_hourly') }} weather using (airport, hour_ts)
