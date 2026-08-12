-- 1:1 typed view over NWS alerts. One row per alert per airport it covers —
-- a single zone alert fans out to every airport in its zone at ingest.
select
    alert_id,
    airport,
    event,
    severity,
    effective_ts,
    ends_ts
from {{ source('skynyc', 'weather_alerts') }}
