-- 1:1 typed view over detected events. No joins, no logic (staging layer rule).
select
    event_id,
    icao24,
    callsign,
    event_type,
    airport,
    event_ts,
    duration_s,
    detected_at
from {{ source('skynyc', 'flight_events') }}
