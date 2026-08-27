"use client";

/** Global filter row: timeframe + airport chips. Chips wear their airport's
 *  fixed hue; the filter changes which series render, never their colors. */

import { SUNSET_DATE } from "@/lib/api";
import { AIRPORT_LABEL, AIRPORT_ORDER, usePaletteOrDark } from "@/lib/palette";
import type { Filters, Timeframe } from "@/lib/useFilters";

const TIMEFRAMES: Timeframe[] = ["3h", "12h", "24h", "7d"];

export default function Controls({ filters, frozen }: { filters: Filters; frozen: boolean }) {
  const P = usePaletteOrDark();
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex overflow-hidden rounded-lg border border-border">
        {TIMEFRAMES.map((t) => (
          <button
            key={t}
            onClick={() => filters.setTimeframe(t)}
            disabled={frozen}
            title={frozen
              ? SUNSET_DATE
                ? "archived — the capture preserves the 24h view"
                : "live API offline — showing the canned 24h capture"
              : undefined}
            className={`px-3 py-1.5 font-mono text-[11.5px] transition-colors ${
              filters.timeframe === t
                ? "bg-surface-2 font-bold text-ink"
                : "text-muted hover:text-ink-2"
            } ${frozen ? "cursor-not-allowed opacity-50" : ""}`}
            aria-pressed={filters.timeframe === t}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex gap-1.5">
        {AIRPORT_ORDER.map((icao) => {
          const on = filters.airports.includes(icao);
          return (
            <button
              key={icao}
              onClick={() => filters.toggleAirport(icao)}
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] font-semibold transition-colors ${
                on ? "border-border-2 text-ink" : "border-border text-faint"
              }`}
              aria-pressed={on}
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ background: on ? P.airport[icao] : P.faint }}
              />
              {AIRPORT_LABEL[icao]}
            </button>
          );
        })}
      </div>
    </div>
  );
}
