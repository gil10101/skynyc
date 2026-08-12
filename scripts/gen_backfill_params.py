"""Generate the ADF backfill parameter files.

BTS on-time reporting starts October 1987; the freshest published month trails
today by roughly two months. Emits the ymList parameter for pl_land_bts and the
station-decade chunks for pl_land_iem, ready for `az datafactory pipeline
create-run --parameters @file`.

Usage: python3 scripts/gen_backfill_params.py <end_year> <end_month> <outdir>
"""
import json
import pathlib
import sys

end_y, end_m = int(sys.argv[1]), int(sys.argv[2])
outdir = pathlib.Path(sys.argv[3])
outdir.mkdir(parents=True, exist_ok=True)

months = []
y, m = 1987, 10
while (y, m) <= (end_y, end_m):
    months.append({"y": str(y), "m": str(m), "ym": f"{y}-{m:02d}"})
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)

# Pipeline parameters are string-typed (azurerm limitation); the pipeline
# parses them with @json(), so the lists are serialized twice on purpose.
(outdir / "bts_backfill.json").write_text(json.dumps({"ymList": json.dumps(months)}))

chunks = []
for station in ("JFK", "LGA", "EWR"):
    for y1 in range(1987, end_y + 1, 10):
        chunks.append({"station": station, "y1": str(y1),
                       "y2": str(min(y1 + 9, end_y))})

(outdir / "iem_backfill.json").write_text(json.dumps({"chunks": json.dumps(chunks)}))

print(f"bts months: {len(months)} (1987-10 .. {end_y}-{end_m:02d}); "
      f"iem chunks: {len(chunks)}")
