-- Grain: one row per airport per flight category per wind bucket.
--
-- The business answer (PRD §1): how NYC arrival operations degrade as
-- conditions worsen. Airport stays in the grain because conditions are
-- per-station and baselines differ — coastal fog can hold KJFK in LIFR while
-- KEWR sits in MVFR, so a pooled rate would partly measure which airport is
-- in the cell, not the weather. Readers aggregate explicitly, with the
-- weighting visible. All rates are per-hour averages over the hours actually
-- observed in that condition, and airport_hours is published alongside every
-- rate so thin cells cannot fake a stable one.
--
-- Wind buckets use effective wind (gust when reported, else sustained):
-- under 20 km/h calm, 20-37 breezy, over 37 windy (roughly <11 / 11-20 /
-- >20 kt). Hours with a category but no wind value are kept as 'unknown'
-- rather than silently pooled into calm. The converse — hours with wind but
-- no derivable category — are excluded by the filter below: category is the
-- primary axis, and a row without it has no cell to land in.
with condition_hours as (
    select
        airport,
        flight_category,
        case
            when coalesce(wind_gust_kmh, wind_speed_kmh) is null then 'unknown'
            when coalesce(wind_gust_kmh, wind_speed_kmh) > 37 then 'windy'
            when coalesce(wind_gust_kmh, wind_speed_kmh) >= 20 then 'breezy'
            else 'calm'
        end as wind_bucket,
        arrivals_detected,
        holding_events,
        holding_minutes,
        go_arounds
    from {{ ref('fct_airport_hourly') }}
    where flight_category is not null
)

select
    airport,
    flight_category,
    wind_bucket,
    count(*) as airport_hours,
    sum(arrivals_detected) as arrivals,
    round(avg(arrivals_detected), 2) as arrivals_per_hour,
    sum(holding_events) as holding_events,
    round(avg(holding_minutes), 2) as holding_min_per_hour,
    sum(go_arounds) as go_arounds,
    round(sum(go_arounds)::numeric
          / nullif(sum(arrivals_detected), 0) * 100, 2) as go_around_rate_pct
from condition_hours
group by 1, 2, 3
