-- 1:1 typed view over METAR observations. No joins, no logic (staging layer
-- rule). Quantitative columns were unit-converted at ingest by reading
-- unitCode (PRD §5.2) — nothing here re-derives units.
select
    station,
    obs_ts,
    temp_c,
    wind_speed_kmh,
    wind_gust_kmh,
    wind_dir_deg,
    visibility_m,
    ceiling_ft,
    precip_last_hr_mm,
    text_desc,
    flight_category
from {{ source('skynyc', 'weather_obs') }}
