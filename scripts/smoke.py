#!/usr/bin/env python3
"""SkyNYC M0 smoke test — PRD §13 M0 acceptance criteria.

Three checks against the live APIs (no mocks — see PRD §2.1):
  1. OpenSky OAuth2 client-credentials token exchange           (PRD §5.1, Manual 01 §A)
  2. One authenticated GET /states/all over the NYC bbox        (PRD §5.1)
  3. One KJFK observation, fresh within 2 h                     (PRD §5.2, Manual 01 §B)

Costs exactly 1 credit from the 4,000/day states bucket.
Standard library only, so it runs before any dependency is installed.
Exits 0 only when all three checks pass. Never prints a token.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
STATES_URL = "https://opensky-network.org/api/states/all"
NWS_OBS_URL = "https://api.weather.gov/stations/{station}/observations/latest"

TIMEOUT_S = 15  # PRD §5.1 producer behavior
DEFAULT_BBOX = "40.0,-75.0,41.4,-72.9"
OBS_MAX_AGE = timedelta(hours=2)  # PRD §13 M0 AC
CREDIT_DEGRADE_FLOOR = 500  # PRD §5.1: below this, producers drop to 60 s polling

OK = "[PASS]"
FAIL = "[FAIL]"


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE, # comments, optional quotes. Never overrides
    a value already exported in the environment."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require_env(*names: str) -> list[str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        print(f"{FAIL} missing in .env: {', '.join(missing)}", file=sys.stderr)
        print("  cp .env.example .env and fill it in (Manual 01)", file=sys.stderr)
        sys.exit(2)
    return [os.environ[n] for n in names]


def http(request: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
    """Return (status, headers, body). HTTP errors are returned, not raised, so a
    403/429 can be reported with its body — those bodies carry the real reason."""
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def jwt_expiry(token: str) -> datetime | None:
    """Read `exp` out of the JWT payload without verifying it — display only."""
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(padded))["exp"]
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except Exception:
        return None


def check_token() -> str | None:
    """Check 1 — OAuth2 client-credentials exchange. Returns the token or None."""
    print("1. OpenSky token exchange")
    client_id, client_secret = require_env("OPENSKY_CLIENT_ID", "OPENSKY_CLIENT_SECRET")
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    status, _, raw = http(
        urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
    )
    if status != 200:
        print(f"   {FAIL} HTTP {status}: {raw[:300].decode(errors='replace')}")
        print("      401/invalid_client => wrong client_id/secret in .env (Manual 01 §A)")
        return None

    payload = json.loads(raw)
    token = payload.get("access_token")
    if not token:
        print(f"   {FAIL} no access_token in response: {sorted(payload)}")
        return None

    expires_in = payload.get("expires_in")
    expiry = jwt_expiry(token)
    print(f"   {OK} bearer token acquired: type={payload.get('token_type')} "
          f"len={len(token)} expires_in={expires_in}s")
    if expiry:
        print(f"      expires at {expiry:%Y-%m-%dT%H:%M:%SZ} "
              f"(TTL 30 min; producers refresh at 25 — PRD §5.1)")
    return token


def check_states(token: str) -> bool:
    """Check 2 — one authenticated /states/all over the bbox. 1 credit."""
    print("2. OpenSky /states/all over the NYC bbox")
    bbox = os.environ.get("BBOX", DEFAULT_BBOX)
    try:
        lamin, lomin, lamax, lomax = (p.strip() for p in bbox.split(","))
    except ValueError:
        print(f"   {FAIL} BBOX must be 'lamin,lomin,lamax,lomax', got {bbox!r}")
        return False

    query = urllib.parse.urlencode(
        {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax, "extended": 1}
    )
    status, headers, raw = http(
        urllib.request.Request(
            f"{STATES_URL}?{query}", headers={"Authorization": f"Bearer {token}"}
        )
    )
    remaining = headers.get("X-Rate-Limit-Remaining")
    if status != 200:
        print(f"   {FAIL} HTTP {status}: {raw[:300].decode(errors='replace')}")
        if status == 429:
            print(f"      credits exhausted; retry after "
                  f"{headers.get('X-Rate-Limit-Retry-After-Seconds')}s (PRD §2.2)")
        return False

    payload = json.loads(raw)
    states = payload.get("states") or []
    api_ts = payload.get("time")
    if not states:
        print(f"   {FAIL} 0 aircraft returned (AC requires > 0). "
              f"api time={api_ts}; a real 03:00-05:00 lull is possible but rare in this box")
        return False

    on_ground = sum(1 for s in states if s[8])
    null_pos = sum(1 for s in states if s[5] is None or s[6] is None)
    age = int(datetime.now(tz=timezone.utc).timestamp()) - int(api_ts) if api_ts else None
    print(f"   {OK} {len(states)} aircraft in bbox {bbox} "
          f"({on_ground} on ground, {len(states) - on_ground} airborne)")
    print(f"      api time={datetime.fromtimestamp(api_ts, tz=timezone.utc):%Y-%m-%dT%H:%M:%SZ} "
          f"(age {age}s), rows with null lat/lon={null_pos} (dropped at parse — PRD §5.1)")

    sample = next((s for s in states if s[1] and s[1].strip() and s[5] is not None), states[0])
    print(f"      sample: icao24={sample[0]} callsign={(sample[1] or '').strip()!r} "
          f"lat={sample[6]} lon={sample[5]} alt_m={sample[7]} on_ground={sample[8]} "
          f"vel_ms={sample[9]} category={sample[17] if len(sample) > 17 else None}")

    if remaining is None:
        print("      note: no X-Rate-Limit-Remaining header returned")
    else:
        print(f"      X-Rate-Limit-Remaining={remaining} of 4,000 states credits "
              f"(< {CREDIT_DEGRADE_FLOOR} => producer degrades to 60 s)")
    return True


def check_observation(station: str = "KJFK") -> bool:
    """Check 3 — one NWS observation, fresh within 2 h. Reads unitCode, never assumes."""
    print(f"3. NWS {station} latest observation")
    (contact,) = require_env("CONTACT_EMAIL")
    # Mandatory descriptive User-Agent — requests without it are rejected (Manual 01 §B).
    status, _, raw = http(
        urllib.request.Request(
            NWS_OBS_URL.format(station=station),
            headers={"User-Agent": f"skynyc ({contact})", "Accept": "application/geo+json"},
        )
    )
    if status != 200:
        print(f"   {FAIL} HTTP {status}: {raw[:300].decode(errors='replace')}")
        if status == 403:
            print("      403 => missing/blank User-Agent; set CONTACT_EMAIL (Manual 01 §B)")
        return False

    props = json.loads(raw).get("properties") or {}
    obs_ts_raw = props.get("timestamp")
    if not obs_ts_raw:
        print(f"   {FAIL} no properties.timestamp in response")
        return False

    obs_ts = datetime.fromisoformat(obs_ts_raw)
    age = datetime.now(tz=timezone.utc) - obs_ts
    fresh = age <= OBS_MAX_AGE
    mark = OK if fresh else FAIL
    print(f"   {mark} obs_ts={obs_ts:%Y-%m-%dT%H:%M:%SZ} "
          f"age={int(age.total_seconds() // 60)} min (AC: < 120 min) "
          f"desc={props.get('textDescription')!r}")

    # Units are load-bearing: read unitCode, never infer from the field name (PRD §5.2).
    for field in ("temperature", "windSpeed", "windGust", "windDirection",
                  "visibility", "precipitationLastHour"):
        entry = props.get(field) or {}
        print(f"      {field}={entry.get('value')} unitCode={entry.get('unitCode')}")
    layers = props.get("cloudLayers") or []
    print(f"      cloudLayers={len(layers)}: " + ", ".join(
        f"{l.get('amount')}@{(l.get('base') or {}).get('value')}"
        f"{(l.get('base') or {}).get('unitCode', '')}" for l in layers) or "      cloudLayers=0")
    if not fresh:
        print("      stale observation — METAR cadence is hourly; > 2 h means a station gap")
    return fresh


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    print(f"SkyNYC smoke test — {datetime.now(tz=timezone.utc):%Y-%m-%dT%H:%M:%SZ} "
          f"(costs 1 states credit)\n")

    token = check_token()
    print()
    states_ok = check_states(token) if token else False
    print()
    obs_ok = check_observation()

    results = {"token": token is not None, "states": states_ok, "observation": obs_ok}
    print("\n" + "-" * 68)
    print("  ".join(f"{OK if passed else FAIL} {name}" for name, passed in results.items()))
    if all(results.values()):
        print("M0 smoke PASS — all three acceptance checks green (PRD §13 M0)")
        return 0
    print("M0 smoke FAIL — see the failing check above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
