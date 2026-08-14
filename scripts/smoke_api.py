#!/usr/bin/env python3
"""Live smoke for the public API (public-dashboard spec §5): every endpoint,
plus the write-refusal and stream checks. Run from anywhere:

    python3 scripts/smoke_api.py [base_url]

Default base is the public vhost; pass http://localhost:8000 when tunneled.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://api.sky.gillu.me"

ENDPOINTS = [
    "/v1/meta",
    "/v1/snapshot",
    "/v1/arrivals?hours=24",
    "/v1/arrivals?hours=3&airport=KJFK",
    "/v1/airborne?hours=24",
    "/v1/events?limit=5",
    "/v1/wind?hours=12",
    "/v1/scatter?days=7",
    "/v1/quality?days=30",
    "/v1/history/staircase?from_year=2000&to_year=2026",
    "/v1/history/causes",
    "/v1/history/seasonality",
    "/v1/history/worst?limit=5",
    "/v1/history/monthly?category=LIFR",
]


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return response.status, json.loads(response.read())


def main() -> int:
    failures = 0
    for path in ENDPOINTS:
        try:
            status, body = get(path)
            ok = status == 200 and "generated_at" in body
            keys = [k for k in body if k != "generated_at"]
            size = sum(len(body[k]) if isinstance(body[k], list) else 1 for k in keys)
            print(f"{'OK ' if ok else 'BAD'} {path}  ({', '.join(keys)}; n={size})")
            failures += 0 if ok else 1
        except Exception as e:  # noqa: BLE001 — smoke reports, doesn't crash
            print(f"BAD {path}  {e}")
            failures += 1

    # GET-only contract
    try:
        request = urllib.request.Request(BASE + "/v1/snapshot", data=b"{}", method="POST")
        urllib.request.urlopen(request, timeout=20)
        print("BAD POST /v1/snapshot accepted (expected 405)")
        failures += 1
    except urllib.error.HTTPError as e:
        ok = e.code == 405
        print(f"{'OK ' if ok else 'BAD'} POST /v1/snapshot -> {e.code}")
        failures += 0 if ok else 1

    # invalid params -> 400
    try:
        urllib.request.urlopen(BASE + "/v1/arrivals?hours=999", timeout=20)
        print("BAD hours=999 accepted (expected 400)")
        failures += 1
    except urllib.error.HTTPError as e:
        ok = e.code == 400
        print(f"{'OK ' if ok else 'BAD'} /v1/arrivals?hours=999 -> {e.code}")
        failures += 0 if ok else 1

    # SSE: first event within ~15 s
    try:
        headers = {"Accept": "text/event-stream"}
        request = urllib.request.Request(BASE + "/v1/stream", headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            chunk = response.read(20)
            ok = chunk.startswith(b"event:")
            print(f"{'OK ' if ok else 'BAD'} /v1/stream first bytes {chunk[:12]!r}")
            failures += 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        print(f"BAD /v1/stream  {e}")
        failures += 1

    print(f"\n{'SMOKE PASS' if failures == 0 else f'SMOKE FAIL ({failures})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
