"use client";

/** NOW row: one card per airport (category, wind, visibility, ceiling, active
 *  alerts) + the freshness stat that mirrors the alerting rule's thresholds. */

import { AIRPORT_LABEL, AIRPORT_ORDER, usePaletteOrDark } from "@/lib/palette";
import type { Live } from "@/lib/useLive";

function fmt(value: number | null | undefined, unit: string, digits = 0): string {
  if (value == null) return "—";
  return `${value.toFixed(digits)}${unit}`;
}

export default function ConditionsRow({ live, airports }: { live: Live; airports: string[] }) {
  const P = usePaletteOrDark();
  const snap = live.snap;
  const freshness = snap?.freshness_s ?? null;
  const freshColor =
    freshness == null || freshness > 900 ? P.bad : freshness > 120 ? P.warn : P.good;

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {AIRPORT_ORDER.filter((a) => airports.includes(a)).map((icao) => {
        const c = snap?.conditions.find((x) => x.station === icao);
        const alerts = snap?.alerts.filter((a) => a.airport === icao) ?? [];
        const cat = c?.flight_category ?? null;
        return (
          <div key={icao} className="rounded-xl border border-border bg-surface p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: P.airport[icao] }} />
                <span className="font-mono text-[13px] font-bold text-ink">{AIRPORT_LABEL[icao]}</span>
              </div>
              <span
                className="rounded-md px-2 py-0.5 font-mono text-[11px] font-bold"
                style={{
                  color: cat ? P.category[cat] : P.neutral,
                  background: cat ? `${P.category[cat]}1f` : `${P.neutral}15`,
                }}
              >
                {cat ?? "no data"}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono text-[11.5px]">
              <span className="text-muted">wind</span>
              <span className="text-ink-2">
                {fmt(c?.wind_speed_kmh, " km/h")}
                {c?.wind_gust_kmh ? ` g${Math.round(c.wind_gust_kmh)}` : ""}
              </span>
              <span className="text-muted">visibility</span>
              <span className="text-ink-2">{c?.visibility_m != null ? `${(c.visibility_m / 1000).toFixed(1)} km` : "—"}</span>
              <span className="text-muted">ceiling</span>
              <span className="text-ink-2">{fmt(c?.ceiling_ft, " ft")}</span>
              <span className="text-muted">sky</span>
              <span className="truncate text-ink-2" title={c?.text_desc ?? undefined}>
                {c?.text_desc ?? "—"}
              </span>
            </div>
            <div className="mt-3 border-t border-border pt-2 text-[11px]">
              {alerts.length === 0 ? (
                <span className="text-faint">no active NWS alerts</span>
              ) : (
                alerts.map((a) => (
                  <div key={a.alert_id} className="flex items-center gap-1.5 text-warn">
                    <span className="h-1.5 w-1.5 rounded-full bg-warn" />
                    {a.event}
                  </div>
                ))
              )}
            </div>
          </div>
        );
      })}

      <div className="rounded-xl border border-border bg-surface p-4 md:col-span-2 xl:col-span-1">
        <div className="font-mono text-[11px] tracking-widest text-muted">PIPELINE FRESHNESS</div>
        <div className="mt-2 font-mono text-[44px] font-bold leading-none" style={{ color: freshColor }}>
          {freshness != null ? `${freshness}s` : "—"}
        </div>
        <p className="mt-2 text-[11.5px] leading-relaxed text-muted">
          seconds since the newest aircraft position reached the database — the same
          signal the ops alert watches (fires at 5 min)
        </p>
      </div>
    </div>
  );
}
