"""Silver: typed, deduplicated on-time performance facts.

Casts the bronze strings to real types, keeps the columns the analysis needs,
and deduplicates on the natural flight key. Delay-cause columns (weather, NAS,
carrier, late-aircraft) only exist from June 2003 onward in the source — they
stay null before that, which downstream marts must treat as "unattributed",
never as zero. Full overwrite each run: at this volume a rebuild is minutes of
single-node time, and it keeps the job trivially idempotent.

Args: <lake_base>
"""
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

LAKE = sys.argv[1]
BRONZE = f"{LAKE}/bronze/bts_ontime"
SILVER = f"{LAKE}/silver/ontime"

spark = SparkSession.builder.getOrCreate()

bronze = spark.read.format("delta").load(BRONZE)

typed = bronze.select(
    F.to_date("FlightDate").alias("flight_date"),
    F.col("Reporting_Airline").alias("carrier"),
    F.col("Flight_Number_Reporting_Airline").alias("flight_number"),
    F.col("Origin").alias("origin"),
    F.col("Dest").alias("dest"),
    F.col("CRSDepTime").alias("crs_dep_time"),
    F.col("CRSArrTime").alias("crs_arr_time"),
    F.col("ArrTime").alias("arr_time"),
    F.col("ArrDelay").cast("double").alias("arr_delay_min"),
    F.col("ArrDel15").cast("double").alias("arr_del15"),
    F.col("Cancelled").cast("double").alias("cancelled"),
    F.col("CancellationCode").alias("cancellation_code"),
    F.col("Diverted").cast("double").alias("diverted"),
    F.col("WeatherDelay").cast("double").alias("weather_delay_min"),
    F.col("NASDelay").cast("double").alias("nas_delay_min"),
    F.col("CarrierDelay").cast("double").alias("carrier_delay_min"),
    F.col("LateAircraftDelay").cast("double").alias("late_aircraft_delay_min"),
    F.col("_ingest_ym"),
).where(F.col("flight_date").isNotNull())

# Natural key: one scheduled flight leg. Ties (rare re-published rows) resolve
# to the newest ingested month so corrections win.
key = ["flight_date", "carrier", "flight_number", "origin", "dest", "crs_dep_time"]
w = Window.partitionBy(*key).orderBy(F.col("_ingest_ym").desc())
deduped = (
    typed.withColumn("_rn", F.row_number().over(w))
    .where(F.col("_rn") == 1)
    .drop("_rn")
    .withColumn("year", F.year("flight_date"))
)

(
    deduped.write.format("delta").mode("overwrite")
    .option("overwriteSchema", "true")
    .partitionBy("year")
    .save(SILVER)
)

# Maintenance is part of the pipeline, not a ceremony: gold's first operation
# filters dest to the NYC three, so co-locating rows by dest lets its scan skip
# most files. Runs after every rebuild because the rebuild replaces every file.
# Delta Python API, not SQL strings: `delta.`path`` SQL resolves through
# spark_catalog, and legacy Hive-metastore access is disabled on UC-default
# workspaces — the path API bypasses catalog resolution entirely.
from delta.tables import DeltaTable  # noqa: E402 - needs the active session

silver_table = DeltaTable.forPath(spark, SILVER)
before = silver_table.detail().select("numFiles").first()[0]
silver_table.optimize().executeZOrderBy("dest")
after = silver_table.detail().select("numFiles").first()[0]

# Retention: each monthly full-overwrite strands the entire previous version as
# stale files, so unvacuumed storage doubles per cycle. 168h keeps roughly two
# rebuilds reachable for time travel while bounding storage near 2x live size.
# Never lower this below the 7-day default check; we deliberately leave
# spark.databricks.delta.retentionDurationCheck.enabled at its safe default.
silver_table.vacuum(168)

rows = spark.read.format("delta").load(SILVER).count()
in_rows = bronze.count()
print(f"silver_ontime done: {in_rows:,} bronze rows -> {rows:,} silver rows "
      f"({in_rows - rows:,} duplicates/nulls dropped); "
      f"optimize compacted {before} -> {after} files")
