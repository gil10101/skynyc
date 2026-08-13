/** Fixed color assignments — hue follows the entity, never its rank or order.
 *  Same trio + aviation-standard category colors as the Grafana boards. */

export const AIRPORT: Record<string, string> = {
  KJFK: "#3987e5",
  KLGA: "#d95926",
  KEWR: "#199e70",
  // BTS codes (historical mart) map to the same hues
  JFK: "#3987e5",
  LGA: "#d95926",
  EWR: "#199e70",
};

export const AIRPORT_LABEL: Record<string, string> = {
  KJFK: "JFK",
  KLGA: "LGA",
  KEWR: "EWR",
  JFK: "JFK",
  LGA: "LGA",
  EWR: "EWR",
};

/** Fixed presentation order everywhere — never re-sorted by value. */
export const AIRPORT_ORDER = ["KJFK", "KLGA", "KEWR"] as const;
export const BTS_ORDER = ["JFK", "LGA", "EWR"] as const;

export const CATEGORY: Record<string, string> = {
  VFR: "#0ca30c",
  MVFR: "#3987e5",
  IFR: "#e34948",
  LIFR: "#9085e9",
};
export const CATEGORY_ORDER = ["VFR", "MVFR", "IFR", "LIFR"] as const;

export const INK = "#e6edf3";
export const INK_2 = "#b3bec9";
export const MUTED = "#7d8894";
export const BORDER = "#2a323d";
export const SURFACE = "#161b22";

/** Altitude ramp for aircraft glyphs (viridis-style, colorblind-safe on dark).
 *  Stops in meters. */
const ALT_STOPS: [number, string][] = [
  [0, "#440154"],
  [1500, "#3b528b"],
  [3000, "#21918c"],
  [6000, "#5ec962"],
  [12000, "#fde725"],
];

export function altitudeColor(altM: number | null): string {
  if (altM == null) return "#7d8894";
  const stops = ALT_STOPS;
  if (altM <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    if (altM <= stops[i][0]) {
      const [a0, c0] = stops[i - 1];
      const [a1, c1] = stops[i];
      return mix(c0, c1, (altM - a0) / (a1 - a0));
    }
  }
  return stops[stops.length - 1][1];
}

function mix(hexA: string, hexB: string, t: number): string {
  const a = parseInt(hexA.slice(1), 16);
  const b = parseInt(hexB.slice(1), 16);
  const channel = (shift: number) =>
    Math.round(((a >> shift) & 255) + (((b >> shift) & 255) - ((a >> shift) & 255)) * t);
  return `#${((channel(16) << 16) | (channel(8) << 8) | channel(0)).toString(16).padStart(6, "0")}`;
}
