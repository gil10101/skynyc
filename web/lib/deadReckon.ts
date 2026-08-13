/** Dead reckoning between SSE ticks: advance a position along its reported
 *  track at its reported ground speed. Flat-earth step is fine at ≤45 s and
 *  terminal-area distances. Clamped so a stale aircraft stops moving instead
 *  of sailing off the map — extrapolation past the clamp would be fiction. */

const EARTH_RADIUS_M = 6_371_000;
export const MAX_EXTRAPOLATE_S = 45;

export interface LatLon {
  lat: number;
  lon: number;
}

export function advance(
  from: LatLon,
  velMs: number | null,
  trackDeg: number | null,
  dtS: number,
): LatLon {
  if (velMs == null || trackDeg == null || velMs <= 0 || dtS <= 0) return from;
  const dt = Math.min(dtS, MAX_EXTRAPOLATE_S);
  const distance = velMs * dt;
  const bearing = (trackDeg * Math.PI) / 180;
  const dLat = (distance * Math.cos(bearing)) / EARTH_RADIUS_M;
  const dLon =
    (distance * Math.sin(bearing)) /
    (EARTH_RADIUS_M * Math.cos((from.lat * Math.PI) / 180));
  return {
    lat: from.lat + (dLat * 180) / Math.PI,
    lon: from.lon + (dLon * 180) / Math.PI,
  };
}
