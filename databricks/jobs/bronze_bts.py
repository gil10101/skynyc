"""Bronze: land BTS on-time zips into Delta, one partition per source month.

Reads the monthly PREZIP archives ADF landed under raw/bts/ym=YYYY-MM/, extracts
the single CSV each contains, and appends to the bronze Delta table with every
column kept as string — bronze preserves the source shape; typing happens in
silver. Idempotent: each month is written with replaceWhere on its _ingest_ym,
so re-runs and partial-failure retries converge instead of duplicating.

Args: <raw_base> <lake_base>   (abfss:// URLs, supplied by the job definition)
"""
import os
import sys
import zipfile

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

RAW, LAKE = sys.argv[1], sys.argv[2]
BRONZE_TABLE = f"{LAKE}/bronze/bts_ontime"
LOCAL_TMP = "/tmp/bts_extract"

spark = SparkSession.builder.getOrCreate()


def loaded_months() -> set:
    try:
        rows = (
            spark.read.format("delta").load(BRONZE_TABLE)
            .select("_ingest_ym").distinct().collect()
        )
        return {r["_ingest_ym"] for r in rows}
    except Exception:
        return set()  # first run — table does not exist yet


def landed_zips() -> list:
    out = []
    for ym_dir in dbutils.fs.ls(f"{RAW}/bts/"):  # noqa: F821 - Databricks builtin
        if not ym_dir.name.startswith("ym="):
            continue
        ym = ym_dir.name.rstrip("/").split("=", 1)[1]
        for f in dbutils.fs.ls(ym_dir.path):  # noqa: F821
            if f.name.endswith(".zip"):
                out.append((ym, f.path))
    return sorted(out)


done = loaded_months()
work = [(ym, path) for ym, path in landed_zips() if ym not in done]
print(f"bronze_bts: {len(done)} months already loaded, {len(work)} to ingest")

os.makedirs(LOCAL_TMP, exist_ok=True)
total_rows = 0

for i, (ym, zpath) in enumerate(work, 1):
    local_zip = f"{LOCAL_TMP}/{ym}.zip"
    dbutils.fs.cp(zpath, f"file:{local_zip}")  # noqa: F821

    with zipfile.ZipFile(local_zip) as z:
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(csvs) != 1:
            raise ValueError(f"{zpath}: expected exactly one CSV, found {csvs}")
        z.extract(csvs[0], LOCAL_TMP)
    local_csv = f"{LOCAL_TMP}/{csvs[0]}"

    df = (
        spark.read.option("header", True).option("multiLine", True)
        .option("escape", '"').csv(f"file:{local_csv}")
        .withColumn("_ingest_ym", F.lit(ym))
    )
    # No pre-write count: it costs a second full parse of every CSV just for a
    # log line. Row counts come from the Delta commit's own metrics instead.
    (
        df.write.format("delta").mode("overwrite")
        .option("replaceWhere", f"_ingest_ym = '{ym}'")
        .option("mergeSchema", "true")
        .partitionBy("_ingest_ym")
        .save(BRONZE_TABLE)
    )
    written = (
        spark.sql(f"DESCRIBE HISTORY delta.`{BRONZE_TABLE}` LIMIT 1")
        .select("operationMetrics").first()[0]
    )
    n = int(written.get("numOutputRows", 0))
    total_rows += n
    os.remove(local_zip)
    os.remove(local_csv)
    print(f"[{i}/{len(work)}] {ym}: {n:,} rows (cumulative new: {total_rows:,})")

final = spark.read.format("delta").load(BRONZE_TABLE).count()
print(f"bronze_bts done: {total_rows:,} rows ingested this run, {final:,} total in bronze")
