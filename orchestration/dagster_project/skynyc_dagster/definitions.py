"""Dagster definitions — batch orchestration only (PRD §9, Manual 07).

Dagster schedules the finite jobs: the daily ground-truth pull and the nightly
Postgres backup now, the hourly dbt build in M4. It does NOT supervise
producers or Spark — Docker restart policies do; an orchestrator's run model is
the wrong shape for an infinite process.

Schedules ship OFF (Dagster default): toggle them on once in the UI under
Automation — until then nothing fires and nothing errors (Manual 07 trap #2).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import requests
from azure.storage.blob import BlobServiceClient
from dagster import (
    AssetKey,
    AssetSelection,
    DailyPartitionsDefinition,
    Definitions,
    RunRequest,
    ScheduleDefinition,
    asset,
    define_asset_job,
    schedule,
)
from dagster_dbt import DbtCliResource, dbt_assets

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


# Asset key matches the table (and therefore the dbt source) it materializes:
# dagster-dbt links stg_arrivals_gt to whatever asset owns the key
# "arrivals_ground_truth" — a mismatched key would leave the pull and the
# staging view as two disconnected nodes over the same table.
@asset(key="arrivals_ground_truth", partitions_def=daily_partitions, group_name="ground_truth")
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

# 09:15 ET daily, pulling a three-day window (D-1 through D-3), not just
# yesterday: the upstream arrivals feed is built by a nightly aggregation that
# can finish hours-to-days late, so a day pulled once at D-1 may be nearly
# empty and would otherwise stay that way forever. Re-pulling the window heals
# late data automatically — the upsert is idempotent, and the cost is
# 3 days x 3 airports x 4 credits = 36/day of the 4,000 flights bucket
# (PRD §2.3). The quality mart withholds scores until a day's ground truth
# reaches plausible volume, so a still-lagging day reads "unscored", never 0%.
@schedule(
    job=ground_truth_job,
    cron_schedule="15 9 * * *",
    execution_timezone="America/New_York",
    name="daily_ground_truth",
)
def daily_ground_truth(context):  # noqa: ANN001, ANN201 — dagster context/yield types
    fire_date = context.scheduled_execution_time.date()
    for days_back in (1, 2, 3):
        key = (fire_date - timedelta(days=days_back)).strftime("%Y-%m-%d")
        yield RunRequest(run_key=f"{fire_date}:{key}", partition_key=key)

BACKUP_CONTAINER = "backups"


@asset(group_name="ops")
def postgres_backup(context) -> None:  # noqa: ANN001 — unannotated per dagster's context rules
    """pg_dump -Fc of the whole database -> Azure blob backups/skynyc_<UTC day>.dump.

    Custom format so a restore can pick tables (pg_restore -t). One blob per
    UTC day, uploaded with overwrite=True: re-running a night replaces that
    day's dump instead of duplicating or erroring. Without Azure creds in the
    environment there is nothing to upload to — warn and skip, so local dev
    never fails on missing cloud config.
    """
    account = os.environ.get("AZURE_STORAGE_ACCOUNT")
    key = os.environ.get("AZURE_STORAGE_KEY")
    if not account or not key:
        context.log.warning(
            "AZURE_STORAGE_ACCOUNT/AZURE_STORAGE_KEY not set — skipping backup upload"
        )
        return

    blob_name = f"skynyc_{datetime.now(timezone.utc):%Y-%m-%d}.dump"
    fd, dump_path = tempfile.mkstemp(prefix="skynyc_", suffix=".dump")
    os.close(fd)
    try:
        # No check=True: CalledProcessError reprs the argv, and the argv carries
        # the DSN — password included. Surface stderr only; never the command.
        result = subprocess.run(
            ["pg_dump", "-Fc", "-f", dump_path, os.environ["PG_DSN"]],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pg_dump failed (rc={result.returncode}): {result.stderr.strip()}"
            )

        service = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net", credential=key
        )
        with open(dump_path, "rb") as dump:
            service.get_blob_client(container=BACKUP_CONTAINER, blob=blob_name).upload_blob(
                dump, overwrite=True
            )
        context.log.info("uploaded %s to %s: %d bytes",
                         blob_name, BACKUP_CONTAINER, os.path.getsize(dump_path))
    finally:
        os.unlink(dump_path)


backup_job = define_asset_job("backup_job", selection=[postgres_backup])

# 03:30 ET nightly — quiet hours, done well before the 09:15 ground-truth pull.
# Ships off like every schedule; toggled once in the UI (Manual 07).
nightly_backup = ScheduleDefinition(
    name="nightly_backup", job=backup_job,
    cron_schedule="30 3 * * *", execution_timezone="America/New_York",
)

# --- Historical lakehouse (PRD §13 M4A, v1.4) -------------------------------
# Dagster is the single control plane: ADF lands raw files on its own trigger,
# but every transform is a Databricks job run that THIS asset starts and waits
# on via the Jobs API. Nothing in Databricks is scheduled from Databricks.

DATABRICKS_POLL_S = 60
DATABRICKS_MAX_WAIT_S = 6 * 3600  # full-history rebuild fits well inside this

UPSERT_GOLD = """
INSERT INTO gold_airport_day_delay (
  flight_date, airport, arrivals_scheduled, cancelled, cancelled_weather,
  avg_arr_delay_min, p90_arr_delay_min, arrivals_delayed_15,
  weather_delay_min_total, nas_delay_min_total, pct_obs_ifr_or_worse,
  min_visibility_sm, min_ceiling_ft, obs_count, worst_category
) VALUES (
  %(flight_date)s, %(airport)s, %(arrivals_scheduled)s, %(cancelled)s, %(cancelled_weather)s,
  %(avg_arr_delay_min)s, %(p90_arr_delay_min)s, %(arrivals_delayed_15)s,
  %(weather_delay_min_total)s, %(nas_delay_min_total)s, %(pct_obs_ifr_or_worse)s,
  %(min_visibility_sm)s, %(min_ceiling_ft)s, %(obs_count)s, %(worst_category)s
)
ON CONFLICT (flight_date, airport) DO UPDATE SET
  arrivals_scheduled = EXCLUDED.arrivals_scheduled,
  cancelled = EXCLUDED.cancelled,
  cancelled_weather = EXCLUDED.cancelled_weather,
  avg_arr_delay_min = EXCLUDED.avg_arr_delay_min,
  p90_arr_delay_min = EXCLUDED.p90_arr_delay_min,
  arrivals_delayed_15 = EXCLUDED.arrivals_delayed_15,
  weather_delay_min_total = EXCLUDED.weather_delay_min_total,
  nas_delay_min_total = EXCLUDED.nas_delay_min_total,
  pct_obs_ifr_or_worse = EXCLUDED.pct_obs_ifr_or_worse,
  min_visibility_sm = EXCLUDED.min_visibility_sm,
  min_ceiling_ft = EXCLUDED.min_ceiling_ft,
  obs_count = EXCLUDED.obs_count,
  worst_category = EXCLUDED.worst_category
"""

GOLD_EXPORT_PREFIX = "gold_export/airport_day_delay/"
GOLD_NUMERIC_INTS = (
    "arrivals_scheduled", "cancelled", "cancelled_weather", "arrivals_delayed_15", "obs_count",
)
GOLD_NUMERIC_FLOATS = (
    "avg_arr_delay_min", "p90_arr_delay_min", "weather_delay_min_total",
    "nas_delay_min_total", "pct_obs_ifr_or_worse", "min_visibility_sm", "min_ceiling_ft",
)


@asset(group_name="lakehouse")
def databricks_medallion_run(context) -> None:  # noqa: ANN001 — unannotated per dagster's context rules
    """Start the skynyc-medallion-build job and wait for a terminal state.

    Uses the Jobs API directly (run-now + poll) — a heavier integration library
    is not warranted for one job. Without workspace credentials in the
    environment there is nothing to trigger — warn and skip, same contract as
    the backup asset.
    """
    host = os.environ.get("DATABRICKS_HOST")
    job_id = os.environ.get("MEDALLION_JOB_ID")
    client_id = os.environ.get("DATABRICKS_CLIENT_ID")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET")
    has_oauth = bool(client_id and client_secret)
    has_pat = bool(os.environ.get("DATABRICKS_TOKEN"))
    if not host or not job_id or not (has_oauth or has_pat):
        context.log.warning(
            "Databricks credentials/MEDALLION_JOB_ID not set — skipping medallion run"
        )
        return

    if has_oauth:
        # OAuth M2M: the service principal mints a one-hour token per run.
        # No long-lived bearer token exists anywhere in the system.
        oauth = requests.post(
            f"{host}/oidc/v1/token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials", "scope": "all-apis"},
            timeout=TIMEOUT_S,
        )
        oauth.raise_for_status()
        token = oauth.json()["access_token"]
    else:
        token = os.environ["DATABRICKS_TOKEN"]

    headers = {"Authorization": f"Bearer {token}"}
    start = requests.post(
        f"{host}/api/2.1/jobs/run-now",
        headers=headers, json={"job_id": int(job_id)}, timeout=TIMEOUT_S,
    )
    start.raise_for_status()
    run_id = start.json()["run_id"]
    context.log.info("started medallion run %s", run_id)

    import time

    waited = 0
    while waited < DATABRICKS_MAX_WAIT_S:
        time.sleep(DATABRICKS_POLL_S)
        waited += DATABRICKS_POLL_S
        run = requests.get(
            f"{host}/api/2.1/jobs/runs/get",
            headers=headers, params={"run_id": run_id}, timeout=TIMEOUT_S,
        )
        run.raise_for_status()
        state = run.json().get("state", {})
        life = state.get("life_cycle_state")
        if life == "TERMINATED":
            result = state.get("result_state")
            if result != "SUCCESS":
                raise RuntimeError(f"medallion run {run_id} finished {result}: "
                                   f"{state.get('state_message', '')}")
            context.log.info("medallion run %s SUCCESS after %ds", run_id, waited)
            return
        if life in ("INTERNAL_ERROR", "SKIPPED"):
            raise RuntimeError(f"medallion run {run_id} ended {life}: "
                               f"{state.get('state_message', '')}")
        if waited % 600 == 0:
            context.log.info("medallion run %s still %s (%ds)", run_id, life, waited)
    raise TimeoutError(f"medallion run {run_id} exceeded {DATABRICKS_MAX_WAIT_S}s")


@asset(group_name="lakehouse", deps=[databricks_medallion_run])
def gold_airport_day_delay_pg(context) -> None:  # noqa: ANN001 — unannotated per dagster's context rules
    """Download the gold CSV export from the lake and upsert into Postgres.

    The mart is small by construction (one row per airport-day), so a CSV
    export + upsert keeps Postgres the only serving store without teaching it
    to read Delta. Empty CSV fields become NULL — never zero.
    """
    import csv
    import io

    account = os.environ.get("AZURE_STORAGE_ACCOUNT")
    key = os.environ.get("AZURE_STORAGE_KEY")
    if not account or not key:
        context.log.warning("Azure credentials not set — skipping gold load")
        return

    service = BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net", credential=key
    )
    container = service.get_container_client("lake")
    parts = [b.name for b in container.list_blobs(name_starts_with=GOLD_EXPORT_PREFIX)
             if b.name.endswith(".csv")]
    if not parts:
        raise FileNotFoundError(f"no CSV under lake/{GOLD_EXPORT_PREFIX} — did gold_marts run?")

    rows: list[dict] = []
    for name in parts:
        text = container.download_blob(name).readall().decode("utf-8")
        for record in csv.DictReader(io.StringIO(text)):
            row = {k: (v if v != "" else None) for k, v in record.items()}
            for col in GOLD_NUMERIC_INTS:
                row[col] = int(float(row[col])) if row.get(col) is not None else None
            for col in GOLD_NUMERIC_FLOATS:
                row[col] = float(row[col]) if row.get(col) is not None else None
            rows.append(row)

    with psycopg.connect(os.environ["PG_DSN"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(UPSERT_GOLD, rows)
        connection.commit()
    context.log.info("upserted %d gold airport-day rows from %d export file(s)",
                     len(rows), len(parts))


lakehouse_job = define_asset_job(
    "lakehouse_job", selection=[databricks_medallion_run, gold_airport_day_delay_pg]
)

# Day 6 monthly, 07:00 ET — the ADF landing trigger runs day 5, so the transform
# sees the new month. Ships off like every schedule (Manual 07).
monthly_lakehouse = ScheduleDefinition(
    name="monthly_lakehouse", job=lakehouse_job,
    cron_schedule="0 7 6 * *", execution_timezone="America/New_York",
)

# --- dbt DAG (PRD §9, M4) ---------------------------------------------------
# The dbt project is discovered from the manifest baked at image build
# (Dockerfile) — every model becomes an asset, dependencies included, nothing
# registered by hand (Manual 06).

DBT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "dbt" / "skynyc_dbt"


@dbt_assets(manifest=DBT_PROJECT_DIR / "target" / "manifest.json")
def skynyc_dbt_models(context, dbt: DbtCliResource):  # noqa: ANN001, ANN201 — dagster-dbt's own signature
    yield from dbt.cli(["build"], context=context).stream()
    # Freshness runs in the same tick, after the build: a green build over
    # stale sources is still a red pipeline, and freshness IS the monitoring
    # story (PRD §9). error-level staleness fails this run on purpose.
    dbt.cli(["source", "freshness"]).wait()


@asset(group_name="ground_truth", deps=[AssetKey("mart_detection_quality")])
def detection_quality_report(context) -> None:  # noqa: ANN001 — unannotated per dagster's context rules
    """Publish the latest fully scored day's precision/recall (PRD §9).

    Reads the quality mart's newest day whose ground truth has landed and logs
    each airport against the P >= 0.85 / R >= 0.80 targets. Warns rather than
    fails below target: a bad score is a tuning signal, not a broken pipeline.
    """
    with psycopg.connect(os.environ["PG_DSN"]) as connection:
        rows = connection.execute(
            """
            select quality_date, airport, arrivals_detected, arrivals_gt,
                   "precision", recall
            from analytics.mart_detection_quality
            where quality_date = (
                select max(quality_date) from analytics.mart_detection_quality
                where "precision" is not null
            )
              and "precision" is not null
            order by airport
            """
        ).fetchall()
    if not rows:
        context.log.warning("no scored days in mart_detection_quality yet")
        return
    for day, airport, detected, gt, precision, recall in rows:
        met = precision >= 0.85 and (recall or 0) >= 0.80
        log = context.log.info if met else context.log.warning
        log("%s %s: precision=%.4f recall=%.4f (detected=%d gt=%d, targets P>=0.85 R>=0.80)",
            day, airport, precision, recall, detected, gt)


dbt_build_job = define_asset_job(
    "dbt_build_job", selection=AssetSelection.assets(skynyc_dbt_models)
)

# Hourly build keeps the marts at most an hour behind the stream (PRD §9).
hourly_dbt = ScheduleDefinition(
    name="hourly_dbt", job=dbt_build_job,
    cron_schedule="0 * * * *", execution_timezone="America/New_York",
)

# 09:45 ET: the 09:15 ground-truth pull has landed and the quality mart must
# be rebuilt WITH the new day before the report reads it — the 09:00 hourly
# build predates the pull, so this job carries its own subsetted rebuild.
daily_quality_job = define_asset_job(
    "daily_quality_job",
    selection=(
        AssetSelection.keys("mart_detection_quality").upstream()
        | AssetSelection.keys("detection_quality_report")
    ),
)

daily_quality = ScheduleDefinition(
    name="daily_quality", job=daily_quality_job,
    cron_schedule="45 9 * * *", execution_timezone="America/New_York",
)

defs = Definitions(
    assets=[ground_truth_arrivals, postgres_backup,
            databricks_medallion_run, gold_airport_day_delay_pg,
            skynyc_dbt_models, detection_quality_report],
    jobs=[ground_truth_job, backup_job, lakehouse_job, dbt_build_job, daily_quality_job],
    schedules=[daily_ground_truth, nightly_backup, monthly_lakehouse,
               hourly_dbt, daily_quality],
    resources={
        "dbt": DbtCliResource(
            project_dir=os.fspath(DBT_PROJECT_DIR),
            profiles_dir=os.fspath(DBT_PROJECT_DIR),
        )
    },
)
