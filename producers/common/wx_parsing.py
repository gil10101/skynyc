"""NWS observation parsing — unitCode-aware, never positional (PRD §5.2).

Every quantitative NWS field arrives as {"value": x, "unitCode": "wmoUnit:..."}.
The converter branches on unitCode; an unknown code yields None and a counted
drop, never a guessed unit. Verified live 2026-08-11: windSpeed arrives as
wmoUnit:km_h-1 (PRD §5.2 wrote km_h — both forms accepted here).
"""

from __future__ import annotations

import logging
from typing import Any

from producers.common.models import WeatherObsMessage

log = logging.getLogger(__name__)

M_PER_STATUTE_MILE = 1609.344
FT_PER_M = 3.28084

# target unit -> accepted unitCodes -> factor applied to value.
# wmoUnit vs unit: prefixes vary across NWS responses; match on the suffix.
_CONVERSIONS: dict[str, dict[str, float]] = {
    "degC": {"degC": 1.0},
    "kmh": {"km_h-1": 1.0, "km_h": 1.0, "m_s-1": 3.6},
    "deg": {"degree_(angle)": 1.0},
    "m": {"m": 1.0},
    "mm": {"mm": 1.0, "m": 1000.0},
    "ft": {"ft": 1.0, "m": FT_PER_M},
}

# Layer amounts that constitute a ceiling: broken, overcast, and vertical
# visibility (obscured sky). FEW/SCT do not set a ceiling.
_CEILING_AMOUNTS = {"BKN", "OVC", "VV"}


def convert(entry: dict[str, Any] | None, target: str) -> float | None:
    """Convert one NWS quantitative entry to the target unit via its unitCode.

    Returns None (and logs) when the value is null or the unitCode is unknown —
    a None is honest; a positional guess is silently wrong (PRD §5.2 gotcha).
    """
    if not entry:
        return None
    value = entry.get("value")
    if value is None:
        return None
    unit_code = entry.get("unitCode") or ""
    suffix = unit_code.split(":", 1)[-1]
    factor = _CONVERSIONS.get(target, {}).get(suffix)
    if factor is None:
        log.warning("unknown unitCode %r for target %s — value dropped", unit_code, target)
        return None
    return float(value) * factor


def ceiling_ft(cloud_layers: list[dict[str, Any]] | None) -> float | None:
    """Lowest BKN/OVC/VV layer base in feet, or None when sky is unlimited."""
    if not cloud_layers:
        return None
    bases = [
        base
        for layer in cloud_layers
        if (layer.get("amount") or "").upper() in _CEILING_AMOUNTS
        and (base := convert(layer.get("base"), "ft")) is not None
    ]
    return min(bases) if bases else None


def flight_category(ceiling: float | None, visibility_m: float | None) -> str | None:
    """FAA flight category from ceiling (ft) and visibility (PRD §5.2 table).

    Category is the worse of the two limits; a missing limit is treated as
    unlimited on that side. Both missing -> None (no data is not VFR).
    """
    if ceiling is None and visibility_m is None:
        return None
    vis_sm = visibility_m / M_PER_STATUTE_MILE if visibility_m is not None else None

    # rank 0=LIFR .. 3=VFR; boundaries exactly per the PRD table
    # (MVFR is 1,000-3,000 ft / 3-5 SM inclusive; VFR is strictly above both).
    def ceiling_rank(c: float | None) -> int:
        if c is None:
            return 3
        if c < 500:
            return 0
        if c < 1000:
            return 1
        if c <= 3000:
            return 2
        return 3

    def vis_rank(v: float | None) -> int:
        if v is None:
            return 3
        if v < 1:
            return 0
        if v < 3:
            return 1
        if v <= 5:
            return 2
        return 3

    return ["LIFR", "IFR", "MVFR", "VFR"][min(ceiling_rank(ceiling), vis_rank(vis_sm))]


def parse_observation(station: str, properties: dict[str, Any]) -> WeatherObsMessage:
    """properties of /stations/{id}/observations/latest -> WeatherObsMessage."""
    ceiling = ceiling_ft(properties.get("cloudLayers"))
    visibility = convert(properties.get("visibility"), "m")
    return WeatherObsMessage(
        station=station,
        obs_ts=properties["timestamp"],
        temp_c=convert(properties.get("temperature"), "degC"),
        wind_speed_kmh=convert(properties.get("windSpeed"), "kmh"),
        wind_gust_kmh=convert(properties.get("windGust"), "kmh"),
        wind_dir_deg=convert(properties.get("windDirection"), "deg"),
        visibility_m=visibility,
        ceiling_ft=ceiling,
        precip_last_hr_mm=convert(properties.get("precipitationLastHour"), "mm"),
        text_desc=properties.get("textDescription"),
        flight_category=flight_category(ceiling, visibility),
        raw=properties,
    )
