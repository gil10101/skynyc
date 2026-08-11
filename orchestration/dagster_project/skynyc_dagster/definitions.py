"""Dagster definitions — batch orchestration only (PRD §9, Manual 07).

Dagster schedules the finite jobs: the daily ground-truth pull now, the hourly
dbt build in M4. It does NOT supervise producers or Spark — Docker restart
policies do; an orchestrator's run model is the wrong shape for an infinite
process.

Schedules ship OFF (Dagster default): toggle them on once in the UI under
Automation — until then nothing fires and nothing errors (Manual 07 trap #2).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
import requests
from dagster import (
    DailyPartitionsDefinition,
    Definitions,
    asset,
    define_asset_job,
    build_schedule_from_partitioned_job,
)

from producers.common.token_manager import TokenManager

ARRIVALS_URL = "https://opensky-network.org/api/flights/arrival"
AIRPORTS = ("KJFK", "KLGA", "KEWR")
TIMEOUT_S = 30

# Ground truth is produced by OpenSky's nightly batch: D-1 is the freshest day
# that exists (PRD §5.1), so partitions end yesterday and the schedule fires
# at 09:15 ET for the previous day.
# ET partition boundaries so the 09:15 ET schedule resolves "yesterday"
# naturally; the asset itself always pulls the UTC day named by the key.
daily_partitions = DailyPartitionsDefinition(
    start_date="2026-08-10", end_offset=0, timezone="America/New_York"
)

UPSERT_GT = """
INSERT INTO arrivals_ground_truth (icao24, airport, callsign, est_arrival_ts, first_seen, last_seen)
VALUES (%(icao24)s, %(airport)s, %(callsign)s, to_timestamp(%(est_arrival_ts)s),
        to_timestamp(%(first_seen)s), to_timestamp(%(last_seen)s))
ON CONFLICT (icao24, airport, est_arrival_ts) DO UPDATE SET
  callsign = EXCLUDED.callsign, first_seen = EXCLUDED.first_seen, last_seen = EXCLUDED.last_seen
"""


@asset(partitions_def=daily_partitions, group_name="ground_truth")
def ground_truth_arrivals(context) -> None:  # noqa: ANN001 — unannotated per dagster's context rules
    """OpenSky /flights/arrival for one UTC day × three airports -> upsert.

    Costs 4 credits per airport call from the independent *flights* bucket
    (12/day of 4,000 — PRD §2.3). 404 means "no flights found" and is an empty
    result, not an error. est_arrival_ts maps from the API's lastSeen — the
    documented end-of-tracking timestamp at the arrival airport.
    """
    day = datetime.strptime(context.partition_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    begin = int(day.timestamp())
    end = int((day + timedelta(days=1)).timestamp())
    tokens = TokenManager()

    rows: list[dict] = []
    for airport in AIRPORTS:
        response = requests.get(
            ARRIVALS_URL,
            params={"airport": airport, "begin": begin, "end": end},
            headers={"Authorization": f"Bearer {tokens.get()}"},
            timeout=TIMEOUT_S,
        )
        remaining = response.headers.get("X-Rate-Limit-Remaining")
        if response.status_code == 404:
            context.log.info("%s %s: no flights (404 = empty, not error); flights credits=%s",
                             airport, context.partition_key, remaining)
            continue
        response.raise_for_status()
        flights = response.json()
        for flight in flights:
            if not flight.get("icao24") or not flight.get("lastSeen"):
                continue
            rows.append({
                "icao24": flight["icao24"],
                "airport": airport,
                "callsign": (flight.get("callsign") or "").strip() or None,
                "est_arrival_ts": flight["lastSeen"],
                "first_seen": flight.get("firstSeen") or flight["lastSeen"],
                "last_seen": flight["lastSeen"],
            })
        context.log.info("%s %s: %d arrivals; flights credits=%s",
                         airport, context.partition_key, len(flights), remaining)

    if rows:
        with psycopg.connect(os.environ["PG_DSN"]) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(UPSERT_GT, rows)
            connection.commit()
    context.log.info("upserted %d ground-truth arrivals for %s", len(rows), context.partition_key)


ground_truth_job = define_asset_job(
    "ground_truth_job", selection=[ground_truth_arrivals], partitions_def=daily_partitions
)

# 09:15 ET daily for the previous day (PRD §9) — timezone comes from the
# partitions definition. Ships off; toggle in the UI.
daily_ground_truth = build_schedule_from_partitioned_job(
    ground_truth_job, name="daily_ground_truth", hour_of_day=9, minute_of_hour=15,
)

defs = Definitions(assets=[ground_truth_arrivals], jobs=[ground_truth_job],
                   schedules=[daily_ground_truth])
