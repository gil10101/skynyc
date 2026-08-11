-- 1:1 typed view over ground-truth arrivals. No joins, no logic.
select
    icao24,
    airport,
    callsign,
    est_arrival_ts,
    first_seen,
    last_seen
from {{ source('skynyc', 'arrivals_ground_truth') }}
