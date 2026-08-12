-- Grain: one row per airport per UTC hour (the spine).
--
-- Weather is three slowly-changing station records, so the value for an hour
-- is the last observation at or before that hour's end — join-at-read, the
-- deliberate alternative to a stream-stream join (PRD §7). Station IDs equal
-- airport ICAO codes, so the join key is direct.
--
-- No staleness cutoff: if the weather producer dies, hours keep carrying the
-- last known observation. That is the correct read semantics; the outage
-- itself is caught by source freshness on weather_obs, not hidden here.
with spine as (
    select airport, hour_ts from {{ ref('int_hours_spine') }}
)

select
    spine.airport,
    spine.hour_ts,
    obs.obs_ts as wx_obs_ts,
    obs.flight_category,
    obs.wind_speed_kmh,
    obs.wind_gust_kmh,
    obs.visibility_m,
    obs.ceiling_ft,
    obs.precip_last_hr_mm,
    alert.active_alert_types
from spine
left join lateral (
    select o.obs_ts, o.flight_category, o.wind_speed_kmh, o.wind_gust_kmh,
           o.visibility_m, o.ceiling_ft, o.precip_last_hr_mm
    from {{ ref('stg_weather_obs') }} o
    where o.station = spine.airport
      and o.obs_ts < spine.hour_ts + interval '1 hour'
    order by o.obs_ts desc
    limit 1
) obs on true
left join lateral (
    -- An alert is active for the hour when its validity overlaps any part of
    -- it. ends_ts already carries the expires fallback from ingest, so the
    -- infinity guard below is belt-and-suspenders for legacy rows, not the
    -- normal path.
    select string_agg(distinct a.event, ', ' order by a.event) as active_alert_types
    from {{ ref('stg_weather_alerts') }} a
    where a.airport = spine.airport
      and a.effective_ts < spine.hour_ts + interval '1 hour'
      and coalesce(a.ends_ts, 'infinity'::timestamptz) > spine.hour_ts
) alert on true
