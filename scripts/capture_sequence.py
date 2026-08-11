#!/usr/bin/env python3
"""Capture real per-aircraft sequences from a topic dump into detector fixtures.

Fixtures are recorded reality, never hand-written (tests/fixtures policy): this
tool reads envelope messages (NDJSON on stdin, e.g. from kafka-console-consumer)
and either scans for candidate sequences or extracts one aircraft's track.

  # list candidates near airports (arrivals, low-altitude disappearances, taxi)
  python3 scripts/capture_sequence.py --scan < dump.ndjson

  # extract one aircraft into a fixture
  python3 scripts/capture_sequence.py --icao24 a4d838 \
      --out tests/fixtures/sequences/arrival_jfk_dal203.json < dump.ndjson

Used during M3 threshold tuning to turn interesting live tracks into permanent
regression cases.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict

AIRPORTS = {"KJFK": (40.6413, -73.7781), "KLGA": (40.7769, -73.8740), "KEWR": (40.6895, -74.1745)}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_airport(lat: float, lon: float) -> tuple[str, float]:
    best = min(AIRPORTS.items(), key=lambda kv: haversine_km(lat, lon, *kv[1]))
    return best[0], haversine_km(lat, lon, *best[1])


def load_tracks() -> dict[str, list[dict]]:
    tracks: dict[str, list[dict]] = defaultdict(list)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("icao24"):
            tracks[m["icao24"]].append(m)
    for t in tracks.values():
        t.sort(key=lambda m: (m["api_ts"], m["poll_ts"]))
    return tracks


def scan(tracks: dict[str, list[dict]]) -> None:
    print(f"{'icao24':8} {'callsign':9} {'n':>4} {'kind':22} {'airport':7} "
          f"{'last_alt':>8} {'last_km':>7} {'span_min':>8}")
    for icao24, track in sorted(tracks.items()):
        pos = [m for m in track if m["lat"] is not None and m["lon"] is not None]
        if len(pos) < 8:
            continue
        last = pos[-1]
        airport, dist = nearest_airport(last["lat"], last["lon"])
        span = (pos[-1]["api_ts"] - pos[0]["api_ts"]) / 60
        airborne = [m for m in pos if not m["on_ground"]]
        landed = last["on_ground"] and any(not m["on_ground"] for m in pos)
        kind = None
        if landed and dist < 4:
            kind = "ARRIVAL (air->ground)"
        elif not last["on_ground"] and (last.get("baro_alt_m") or 9e9) < 450 and dist < 5:
            kind = "COVERAGE-LOSS FINAL?"
        elif all(m["on_ground"] for m in pos):
            kind = "TAXI-ONLY"
        elif airborne and min((m.get("baro_alt_m") or 9e9) for m in airborne) > 2500:
            kind = "OVERFLIGHT (high)"
        if kind:
            callsign = (last.get("callsign") or "").strip() or "-"
            print(f"{icao24:8} {callsign:9} {len(pos):>4} {kind:22} {airport:7} "
                  f"{str(last.get('baro_alt_m')):>8} {dist:>7.1f} {span:>8.1f}")


def extract(tracks: dict[str, list[dict]], icao24: str, out: str) -> None:
    track = tracks.get(icao24)
    if not track:
        sys.exit(f"no messages for {icao24}")
    with open(out, "w") as f:
        json.dump({"icao24": icao24, "n": len(track), "messages": track}, f, indent=1)
    print(f"wrote {len(track)} messages -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--icao24")
    ap.add_argument("--out")
    args = ap.parse_args()
    tracks = load_tracks()
    if args.scan:
        scan(tracks)
    elif args.icao24 and args.out:
        extract(tracks, args.icao24, args.out)
    else:
        ap.error("use --scan, or --icao24 with --out")


if __name__ == "__main__":
    main()
