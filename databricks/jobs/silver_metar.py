"""Silver: parsed METAR observations with derived flight category.

Parses the IEM ASOS CSVs landed under raw/iem/station=KXXX/ into typed
observations and derives the FAA flight category with the same boundaries the
live weather parser uses (PRD §5.2): ceiling is the lowest broken/overcast/
vertical-visibility layer base. IEM conveniently serves visibility in statute
miles and layer bases in feet — no unit conversion needed, but 'M' means
missing everywhere and must become null, never zero.

Args: <raw_base> <lake_base>
"""
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

RAW, LAKE = sys.argv[1], sys.argv[2]
SILVER = f"{LAKE}/silver/metar"

spark = SparkSession.builder.getOrCreate()

raw = (
    spark.read.option("header", True)
    .csv(f"{RAW}/iem/station=*/asos_*.csv")
)


def num(col):
    """IEM missing marker 'M' (and trace 'T') to null; otherwise numeric."""
    return F.when(F.col(col).isin("M", "T", ""), None).otherwise(F.col(col)).cast("double")


CEILING_COVERS = ("BKN", "OVC", "VV")

# Lowest layer whose coverage counts as a ceiling. Coverage strings arrive with
# trailing spaces ("VV ") — trim before comparing.
ceiling_expr = F.least(*[
    F.when(F.trim(F.col(f"skyc{i}")).isin(*CEILING_COVERS), num(f"skyl{i}"))
    for i in range(1, 5)
])

obs = raw.select(
    F.col("station"),
    F.to_timestamp("valid", "yyyy-MM-dd HH:mm").alias("obs_ts"),
    num("vsby").alias("visibility_sm"),
    ceiling_expr.alias("ceiling_ft"),
    F.col("wxcodes"),
).where(F.col("obs_ts").isNotNull())

# PRD §5.2 boundaries. An observation missing BOTH ceiling and visibility gets
# null category (unknown), not VFR — absence of evidence is not clear weather.
cat = (
    F.when((F.col("ceiling_ft") < 500) | (F.col("visibility_sm") < 1), "LIFR")
    .when((F.col("ceiling_ft") < 1000) | (F.col("visibility_sm") < 3), "IFR")
    .when((F.col("ceiling_ft") <= 3000) | (F.col("visibility_sm") <= 5), "MVFR")
    .when(F.col("ceiling_ft").isNotNull() | F.col("visibility_sm").isNotNull(), "VFR")
)

silver = (
    obs.withColumn("flight_category", cat)
    .dropDuplicates(["station", "obs_ts"])
)

(
    silver.write.format("delta").mode("overwrite")
    .option("overwriteSchema", "true")
    .partitionBy("station")
    .save(SILVER)
)

rows = spark.read.format("delta").load(SILVER).count()
by_cat = {r["flight_category"]: r["count"]
          for r in silver.groupBy("flight_category").count().collect()}
print(f"silver_metar done: {rows:,} observations; category counts: {by_cat}")
