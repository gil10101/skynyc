/** Theme-aware palettes. Chrome tokens live in CSS custom properties
 *  (globals.css); charts and the canvas map need literal hex, so the data
 *  palette lives here, per theme, consumed through usePalette().
 *
 *  Dark data hues come from the owner's editor palette; light from Cursor
 *  Light's semantic colors. Fixed assignment — hue follows the entity, never
 *  its rank — and every colored mark ships with a text label, since some
 *  pairs in these palettes sit below the colorblind-separation floor
 *  (owner-accepted trade for palette cohesion). */

import type { Theme } from "./theme";
import { useTheme } from "./useTheme";

export interface DataPalette {
  airport: Record<string, string>;
  category: Record<string, string>;
  eventType: Record<string, string>;
  good: string;
  warn: string;
  bad: string;
  neutral: string;
  faint: string;
  grid: string;
  axisText: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  causes: { weather: string; nas: string };
  /** altitude ramp stops for the plane glyphs, meters -> hex */
  altStops: [number, string][];
  glyphHalo: string;
}

const DARK: DataPalette = {
  airport: {
    KJFK: "#85C1FC", KLGA: "#EFB080", KEWR: "#A3BE8C",
    JFK: "#85C1FC", LGA: "#EFB080", EWR: "#A3BE8C",
  },
  category: { VFR: "#A3BE8C", MVFR: "#85C1FC", IFR: "#BF616A", LIFR: "#AA9BF5" },
  eventType: { arrival: "#A3BE8C", holding: "#EBC88D", go_around: "#BF616A" },
  good: "#A3BE8C",
  warn: "#EBC88D",
  bad: "#BF616A",
  neutral: "#8E959E",
  faint: "#505050",
  grid: "#2E2E2E",
  axisText: "#8E959E",
  tooltipBg: "#1F1F1F",
  tooltipBorder: "#2E2E2E",
  tooltipText: "#D8DEE9",
  causes: { weather: "#BF616A", nas: "#EFB080" },
  altStops: [
    [0, "#85C1FC"], [1500, "#88C0D0"], [3000, "#A3BE8C"],
    [6000, "#EBC88D"], [12000, "#BF616A"],
  ],
  glyphHalo: "rgba(26,26,26,0.9)",
};

const LIGHT: DataPalette = {
  airport: {
    KJFK: "#2563eb", KLGA: "#d97700", KEWR: "#059669",
    JFK: "#2563eb", LGA: "#d97700", EWR: "#059669",
  },
  category: { VFR: "#059669", MVFR: "#2563eb", IFR: "#cc0000", LIFR: "#7c3aed" },
  eventType: { arrival: "#059669", holding: "#d97700", go_around: "#cc0000" },
  good: "#059669",
  warn: "#d97700",
  bad: "#cc0000",
  neutral: "#666666",
  faint: "#999999",
  grid: "#e0e0e0",
  axisText: "#666666",
  tooltipBg: "#ffffff",
  tooltipBorder: "#e0e0e0",
  tooltipText: "#1a1a1a",
  causes: { weather: "#cc0000", nas: "#d97700" },
  altStops: [
    [0, "#2563eb"], [1500, "#0088aa"], [3000, "#059669"],
    [6000, "#d97700"], [12000, "#cc0000"],
  ],
  glyphHalo: "rgba(255,255,255,0.9)",
};

export function paletteFor(theme: Theme): DataPalette {
  return theme === "light" ? LIGHT : DARK;
}

/** null while the theme is unresolved (SSR) — gate chart rendering on it. */
export function usePalette(): DataPalette | null {
  const theme = useTheme();
  return theme ? paletteFor(theme) : null;
}

/** Charts re-render when the theme resolves/changes; SSR paints no marks, so
 *  the pre-resolution default is never visible. The map (canvas + tiles)
 *  still gates + remounts explicitly. */
export function usePaletteOrDark(): DataPalette {
  return usePalette() ?? DARK;
}

export const AIRPORT_LABEL: Record<string, string> = {
  KJFK: "JFK", KLGA: "LGA", KEWR: "EWR",
  JFK: "JFK", LGA: "LGA", EWR: "EWR",
};

/** Fixed presentation order everywhere — never re-sorted by value. */
export const AIRPORT_ORDER = ["KJFK", "KLGA", "KEWR"] as const;
export const BTS_ORDER = ["JFK", "LGA", "EWR"] as const;
export const CATEGORY_ORDER = ["VFR", "MVFR", "IFR", "LIFR"] as const;

export function altitudeColor(p: DataPalette, altM: number | null): string {
  if (altM == null) return p.neutral;
  const stops = p.altStops;
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
