"""Gold: day x airport weather-delay mart for the NYC three.

Joins the arrival-side on-time aggregates to the day's observed weather per
airport. Grain: one row per (flight_date, airport). Also exports the mart as a
single CSV under gold_export/ — that file is what the Dagster asset downloads
and upserts into Postgres, keeping the serving store the only thing dashboards
touch.

Args: <lake_base>
"""
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

LAKE = sys.argv[1]
GOLD = f"{LAKE}/gold/airport_day_delay"
EXPORT = f"{LAKE}/gold_export/airport_day_delay"

NYC = {"JFK": "KJFK", "LGA": "KLGA", "EWR": "KEWR"}
SEVERITY = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}

spark = SparkSession.builder.getOrCreate()

ontime = spark.read.format("delta").load(f"{LAKE}/silver/ontime")
metar = spark.read.format("delta").load(f"{LAKE}/silver/metar")

arrivals = (
    ontime.where(F.col("dest").isin(*NYC))
    .groupBy(F.col("flight_date"), F.col("dest").alias("airport"))
    .agg(
        F.count("*").alias("arrivals_scheduled"),
        F.sum(F.when(F.col("cancelled") == 1.0, 1).otherwise(0)).alias("cancelled"),
        F.sum(F.when(F.col("cancellation_code") == "B", 1).otherwise(0)).alias("cancelled_weather"),
        F.avg("arr_delay_min").alias("avg_arr_delay_min"),
        F.expr("percentile_approx(arr_delay_min, 0.9)").alias("p90_arr_delay_min"),
        F.sum(F.when(F.col("arr_del15") == 1.0, 1).otherwise(0)).alias("arrivals_delayed_15"),
        F.sum("weather_delay_min").alias("weather_delay_min_total"),
        F.sum("nas_delay_min").alias("nas_delay_min_total"),
    )
)

sev = F.create_map([F.lit(x) for kv in SEVERITY.items() for x in kv])
station_to_airport = F.create_map([F.lit(x) for k, v in NYC.items() for x in (v[1:], k)])

# IEM station ids arrive without the K prefix (JFK, LGA, EWR).
wx_daily = (
    metar.withColumn("airport", station_to_airport[F.col("station")])
    .where(F.col("airport").isNotNull() & F.col("flight_category").isNotNull())
    .withColumn("severity", sev[F.col("flight_category")])
    .groupBy(F.to_date("obs_ts").alias("flight_date"), "airport")
    .agg(
        F.max("severity").alias("worst_severity"),
        F.avg(F.when(F.col("severity") >= 2, 1.0).otherwise(0.0)).alias("pct_obs_ifr_or_worse"),
        F.min("visibility_sm").alias("min_visibility_sm"),
        F.min("ceiling_ft").alias("min_ceiling_ft"),
        F.count("*").alias("obs_count"),
    )
)

inv_sev = F.create_map([F.lit(x) for k, v in SEVERITY.items() for x in (v, k)])
gold = (
    arrivals.join(wx_daily, ["flight_date", "airport"], "left")
    .withColumn("worst_category", inv_sev[F.col("worst_severity")])
    .drop("worst_severity")
)

gold.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(GOLD)

# Single-file CSV export for the Postgres loader. Small by construction:
# (~39 years x 366 days x 3 airports) is under 45k rows.
gold.coalesce(1).write.mode("overwrite").option("header", True).csv(EXPORT)

n = gold.count()
span = gold.agg(F.min("flight_date"), F.max("flight_date")).first()
print(f"gold_marts done: {n:,} airport-day rows, {span[0]} .. {span[1]}")
