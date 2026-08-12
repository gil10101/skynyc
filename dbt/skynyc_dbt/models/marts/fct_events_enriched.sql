-- Grain: one row per detected event (event_id).
--
-- Each event carries the weather observation valid at its timestamp:
-- obs_ts <= event_ts < next obs_ts (join-at-read, PRD §7). The lateral top-1
-- implements that interval directly — no as-of window gymnastics. Events
-- before the first observation keep null weather rather than borrowing a
-- future one.
select
    events.event_id,
    events.icao24,
    events.callsign,
    events.event_type,
    events.airport,
    events.event_ts,
    events.duration_s,
    weather.obs_ts as wx_obs_ts,
    round(extract(epoch from events.event_ts - weather.obs_ts) / 60.0) as wx_age_min,
    weather.flight_category,
    weather.wind_speed_kmh,
    weather.wind_gust_kmh,
    weather.visibility_m,
    weather.ceiling_ft,
    weather.precip_last_hr_mm,
    weather.text_desc
from {{ ref('stg_flight_events') }} events
left join lateral (
    select o.obs_ts, o.flight_category, o.wind_speed_kmh, o.wind_gust_kmh,
           o.visibility_m, o.ceiling_ft, o.precip_last_hr_mm, o.text_desc
    from {{ ref('stg_weather_obs') }} o
    where o.station = events.airport
      and o.obs_ts <= events.event_ts
    order by o.obs_ts desc
    limit 1
) weather on true
