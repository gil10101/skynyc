"""SkyNYC Spark Structured Streaming app — M2 queries (PRD §7).

Two queries, each with its own checkpoint directory (Manual 04):

  Q1 `bronze` (trigger 60 s): Kafka -> parse with the explicit envelope schema
      -> drop null lat/lon -> dedupe on (icao24, api_ts) under a 2-minute
      watermark -> append Parquet partitioned dt=/hr=. The archive is the system
      of record — the API cannot backfill (PRD §2.2).
  Q2 `live` (trigger 10 s): latest record per icao24 per micro-batch ->
      foreachBatch upsert into live_states. Powers the map.

Q3 `events` (stateful detectors) lands in M3.

Offsets live in each query's checkpoint, NOT in a Kafka consumer group —
consumer-group tooling neither shows nor resets these queries (Manual 04).

Bronze destination: abfss://<container>@<account>.dfs.core.windows.net/states
when AZURE_STORAGE_ACCOUNT+KEY are set (PRD §8 v1.3); local data/bronze/states
otherwise — same layout, one path swap, documented fallback.
"""

from __future__ import annotations

import logging
import os

from pyspark.sql import DataFrame, SparkSession, functions as F
from pyspark.sql.types import (
    BooleanType, DoubleType, IntegerType, LongType, StringType, StructField, StructType,
)
from pyspark.sql.window import Window

from streaming.sinks.pg import upsert_live_states

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


def read_states(spark: SparkSession) -> DataFrame:
    """Kafka source -> parsed, typed envelope rows with an event-time column."""
    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", os.environ.get("KAFKA_BOOTSTRAP", "kafka:19092"))
        .option("subscribe", TOPIC)
        .option("startingOffsets", "earliest")  # applies only on a fresh checkpoint (Manual 04)
        .option("maxOffsetsPerTrigger", 50000)  # bound replay batch size after downtime
        .load()
    )
    return (
        raw.select(F.from_json(F.col("value").cast("string"), ENVELOPE_SCHEMA).alias("m"))
        .select("m.*")
        .withColumn("api_time", F.to_timestamp(F.from_unixtime("api_ts")))
    )


def start_bronze(spark: SparkSession) -> None:
    """Q1 — immutable Parquet archive, partitioned dt=/hr= (PRD §7)."""
    states = read_states(spark)
    deduped = (
        states.filter(F.col("lat").isNotNull() & F.col("lon").isNotNull())  # drops counted upstream
        .withWatermark("api_time", "2 minutes")
        .dropDuplicates(["icao24", "api_ts"])
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    spark = build_session()
    spark.sparkContext.setLogLevel("WARN")
    log.info("starting queries: bronze -> %s, live -> live_states", bronze_path())
    start_bronze(spark)
    start_live(spark)
    spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
