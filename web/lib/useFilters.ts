"use client";

/** URL-synced global filters (?t=24h&ap=jfk,lga) so any view is shareable.
 *  Timeframe drives the live-section queries; airport chips filter every panel. */

import { useCallback, useEffect, useState } from "react";
import { AIRPORT_ORDER } from "./palette";

export type Timeframe = "3h" | "12h" | "24h" | "7d";
export const TIMEFRAME_HOURS: Record<Timeframe, number> = { "3h": 3, "12h": 12, "24h": 24, "7d": 168 };

const SHORT: Record<string, string> = { KJFK: "jfk", KLGA: "lga", KEWR: "ewr" };
const LONG: Record<string, string> = { jfk: "KJFK", lga: "KLGA", ewr: "KEWR" };

export interface Filters {
  timeframe: Timeframe;
  hours: number;
  airports: string[]; // ICAO, fixed order
  setTimeframe: (t: Timeframe) => void;
  toggleAirport: (icao: string) => void;
}

function readUrl(): { timeframe: Timeframe; airports: string[] } {
  if (typeof window === "undefined") return { timeframe: "24h", airports: [...AIRPORT_ORDER] };
  const q = new URLSearchParams(window.location.search);
  const t = q.get("t");
  const timeframe: Timeframe = t === "3h" || t === "12h" || t === "24h" || t === "7d" ? t : "24h";
  const ap = (q.get("ap") ?? "")
    .split(",")
    .map((s) => LONG[s.trim()])
    .filter(Boolean);
  const airports = ap.length ? AIRPORT_ORDER.filter((a) => ap.includes(a)) : [...AIRPORT_ORDER];
  return { timeframe, airports };
}

export function useFilters(): Filters {
  const [{ timeframe, airports }, setState] = useState(readUrl);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (timeframe === "24h") q.delete("t");
    else q.set("t", timeframe);
    if (airports.length === AIRPORT_ORDER.length) q.delete("ap");
    else q.set("ap", airports.map((a) => SHORT[a]).join(","));
    const query = q.toString();
    window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
  }, [timeframe, airports]);

  const setTimeframe = useCallback((t: Timeframe) => setState((s) => ({ ...s, timeframe: t })), []);
  const toggleAirport = useCallback(
    (icao: string) =>
      setState((s) => {
        const on = s.airports.includes(icao);
        // never allow an empty set — the last chip stays on
        if (on && s.airports.length === 1) return s;
        const airports = AIRPORT_ORDER.filter((a) =>
          a === icao ? !on : s.airports.includes(a),
        );
        return { ...s, airports };
      }),
    [],
  );

  return { timeframe, hours: TIMEFRAME_HOURS[timeframe], airports, setTimeframe, toggleAirport };
}
