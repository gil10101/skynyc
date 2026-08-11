"""SkyNYC Spark Structured Streaming app — queries Q1-Q3 (PRD §7).

Three queries, each with its own checkpoint directory (Manual 04):

  Q1 `bronze` (trigger 60 s): Kafka -> parse with the explicit envelope schema
      -> drop null lat/lon -> dedupe on (icao24, api_ts) under a 2-minute
      watermark -> append Parquet partitioned dt=/hr=. The archive is the system
      of record — the API cannot backfill (PRD §2.2).
  Q2 `live` (trigger 10 s): latest record per icao24 per micro-batch ->
      foreachBatch upsert into live_states. Powers the map.
  Q3 `events` (trigger 30 s): groupBy(icao24).applyInPandasWithState over the
      pure-python detector engine -> idempotent upsert into flight_events.
      90 s processing-time timeout confirms coverage-loss arrivals.

Offsets live in each query's checkpoint, NOT in a Kafka consumer group —
consumer-group tooling neither shows nor resets these queries (Manual 04).
Replay for Q3: delete checkpoints/events AND set EVENTS_REPLAY_FROM (epoch ms)
together — either alone silently no-ops (replay runbook, Manual 04).

Bronze destination: abfss://<container>@<account>.dfs.core.windows.net/states
when AZURE_STORAGE_ACCOUNT+KEY are set (PRD §8 v1.3); local data/bronze/states
otherwise — same layout, one path swap, documented fallback.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterator

from pyspark.sql import DataFrame, SparkSession, functions as F
from pyspark.sql.streaming.state import GroupState, GroupStateTimeout
from pyspark.sql.types import (
    BooleanType, DoubleType, IntegerType, LongType, StringType, StructField, StructType,
)
from pyspark.sql.window import Window

from streaming.detectors import engine
from streaming.sinks.pg import upsert_flight_events, upsert_live_states

log = logging.getLogger("spark_app")

# Explicit schema = the producer envelope (producers/common/models.py). A field
# added there without a matching change here silently reads as null — keep them
# in lockstep.
ENVELOPE_SCHEMA = StructType([
    StructField("poll_ts", LongType()),
    StructField("api_ts", LongType()),
    StructField("icao24", StringType()),
    StructField("callsign", StringType()),
    StructField("lon", DoubleType()),
    StructField("lat", DoubleType()),
    StructField("baro_alt_m", DoubleType()),
    StructField("on_ground", BooleanType()),
    StructField("velocity_ms", DoubleType()),
    StructField("track_deg", DoubleType()),
    StructField("vrate_ms", DoubleType()),
    StructField("category", IntegerType()),
])

TOPIC = "adsb.states.raw"


def bronze_path() -> str:
    account = os.environ.get("AZURE_STORAGE_ACCOUNT", "")
    container = os.environ.get("AZURE_STORAGE_CONTAINER", "bronze")
    if account and os.environ.get("AZURE_STORAGE_KEY"):
        return f"abfss://{container}@{account}.dfs.core.windows.net/states"
    log.warning("AZURE_STORAGE_* not set — bronze falls back to local data/bronze/states")
    return "data/bronze/states"


def build_session() -> SparkSession:
    builder = (
        SparkSession.builder.appName("skynyc-streaming")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "8")  # local[*], tiny volume
    )
    account = os.environ.get("AZURE_STORAGE_ACCOUNT", "")
    key = os.environ.get("AZURE_STORAGE_KEY", "")
    if account and key:
        # Account-key auth for ADLS Gen2 (hadoop-azure abfss connector).
        builder = builder.config(
            f"spark.hadoop.fs.azure.account.key.{account}.dfs.core.windows.net", key
        )
    return builder.getOrCreate()


def read_states(spark: SparkSession, replay_from_ms: str | None = None) -> DataFrame:
    """Kafka source -> parsed, typed envelope rows with an event-time column.

    replay_from_ms maps to startingOffsetsByTimestamp — honored only when the
    query starts with a FRESH checkpoint; ignored otherwise (Manual 04)."""
    reader = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", os.environ.get("KAFKA_BOOTSTRAP", "kafka:19092"))
        .option("subscribe", TOPIC)
        .option("maxOffsetsPerTrigger", 50000)  # bound replay batch size after downtime
    )
    if replay_from_ms:
        # Topic partitions can only grow; an env override beats a silent
        # mismatch that would replay a subset of partitions.
        partitions = int(os.environ.get("KAFKA_STATES_PARTITIONS", "3"))
        reader = reader.option(
            "startingOffsetsByTimestamp",
            json.dumps({TOPIC: {str(p): int(replay_from_ms) for p in range(partitions)}}),
        )
    else:
        reader = reader.option("startingOffsets", "earliest")
    return (
        reader.load()
        .select(F.from_json(F.col("value").cast("string"), ENVELOPE_SCHEMA).alias("m"))
        .select("m.*")
        .withColumn("api_time", F.to_timestamp(F.from_unixtime("api_ts")))
    )


def start_bronze(spark: SparkSession) -> None:
    """Q1 — immutable Parquet archive, partitioned dt=/hr= (PRD §7)."""
    states = read_states(spark)
    deduped = (
        states.filter(F.col("lat").isNotNull() & F.col("lon").isNotNull())  # drops counted upstream
        .withWatermark("api_time", "2 minutes")
        # Dedup state evicts only when the watermark column participates —
        # dropDuplicatesWithinWatermark exists for exactly this (plain
        # dropDuplicates on a non-watermark subset grows state forever).
        .dropDuplicatesWithinWatermark(["icao24", "api_ts"])
        .withColumn("dt", F.date_format("api_time", "yyyy-MM-dd"))
        .withColumn("hr", F.date_format("api_time", "HH"))
    )
    (
        deduped.writeStream.queryName("bronze")
        .format("parquet")
        .option("path", bronze_path())
        .option("checkpointLocation", "checkpoints/bronze")
        .partitionBy("dt", "hr")
        .outputMode("append")
        .trigger(processingTime="60 seconds")
        .start()
    )


def start_live(spark: SparkSession) -> None:
    """Q2 — latest position per aircraft, upserted into live_states (PRD §7)."""
    states = read_states(spark)

    def upsert_batch(batch: DataFrame, batch_id: int) -> None:
        # Latest row per icao24 within the micro-batch; the sink's updated_at
        # guard handles ordering across batches.
        latest = (
            batch.withColumn(
                "rn",
                F.row_number().over(
                    Window.partitionBy("icao24").orderBy(F.desc("api_ts"), F.desc("poll_ts"))
                ),
            )
            .filter("rn = 1")
            .drop("rn")
        )
        rows = [
            {
                "icao24": r.icao24, "callsign": r.callsign,
                "latitude": r.lat, "longitude": r.lon,
                "baro_alt_m": r.baro_alt_m, "on_ground": r.on_ground,
                "velocity_ms": r.velocity_ms, "track_deg": r.track_deg,
                "vrate_ms": r.vrate_ms, "category": r.category,
                "updated_at": r.api_time,
            }
            for r in latest.collect()
            if r.lat is not None and r.lon is not None
        ]
        upsert_live_states(rows)

    (
        states.writeStream.queryName("live")
        .foreachBatch(upsert_batch)
        .option("checkpointLocation", "checkpoints/live")
        .trigger(processingTime="10 seconds")
        .start()
    )


EVENTS_OUTPUT_SCHEMA = StructType([
    StructField("event_id", StringType()),
    StructField("icao24", StringType()),
    StructField("callsign", StringType()),
    StructField("event_type", StringType()),
    StructField("airport", StringType()),
    StructField("event_ts", LongType()),
    StructField("duration_s", IntegerType()),
    StructField("details", StringType()),  # JSON string -> jsonb at the sink
])
EVENTS_STATE_SCHEMA = StructType([StructField("state_json", StringType())])


def detect_events(key: tuple, batches: Iterator, state: GroupState) -> Iterator:
    """applyInPandasWithState bridge: pandas in/out, pure engine in the middle."""
    import pandas as pd  # worker-side import

    # The grouping key is the authoritative identity on both paths — the state
    # blob alone must never be trusted for it (pre-icao24 blobs carry none).
    icao24 = key[0]
    # GroupState.getOption is a PROPERTY yielding the state tuple or None —
    # calling it like a method crashes on the first batch that carries state.
    stored = state.getOption
    current = engine.decode_state(stored[0] if stored else None)

    if state.hasTimedOut:
        _, events = engine.process(current, [], icao24=icao24, timed_out=True)
        state.remove()
    else:
        messages: list[dict[str, Any]] = []
        for pdf in batches:
            for record in pdf.to_dict("records"):
                record["icao24"] = icao24
                messages.append(record)
        messages.sort(key=lambda m: m["api_ts"])
        current, events = engine.process(current, messages, icao24=icao24)
        if current is None:
            state.remove()
        else:
            state.update((engine.encode_state(current),))
            state.setTimeoutDuration(engine.STATE_TIMEOUT_MS)

    if events:
        yield pd.DataFrame([
            {**e, "details": json.dumps(e["details"] or {})} for e in events
        ])


def start_events(spark: SparkSession) -> None:
    """Q3 — stateful per-aircraft detection (PRD §7). The replay runbook's
    EVENTS_REPLAY_FROM lands here as startingOffsetsByTimestamp."""
    states = read_states(spark, replay_from_ms=os.environ.get("EVENTS_REPLAY_FROM"))
    detected = (
        states.select("poll_ts", "api_ts", "icao24", "callsign", "lon", "lat",
                      "baro_alt_m", "on_ground", "velocity_ms", "track_deg",
                      "vrate_ms", "category")
        .groupBy("icao24")
        .applyInPandasWithState(
            detect_events,
            outputStructType=EVENTS_OUTPUT_SCHEMA,
            stateStructType=EVENTS_STATE_SCHEMA,
            outputMode="update",
            timeoutConf=GroupStateTimeout.ProcessingTimeTimeout,
        )
    )

    def sink(batch: DataFrame, batch_id: int) -> None:
        rows = [
            {**r.asDict(), "details": json.loads(r.details or "{}")}
            for r in batch.collect()
        ]
        upsert_flight_events(rows)
        if rows:
            log.info("events batch %d: %d event(s) upserted", batch_id, len(rows))

    (
        detected.writeStream.queryName("events")
        .foreachBatch(sink)
        .option("checkpointLocation", "checkpoints/events")
        .outputMode("update")
        .trigger(processingTime="30 seconds")
        .start()
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    spark = build_session()
    spark.sparkContext.setLogLevel("WARN")
    log.info("starting queries: bronze -> %s, live -> live_states, events -> flight_events",
             bronze_path())
    start_bronze(spark)
    start_live(spark)
    start_events(spark)
    spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
