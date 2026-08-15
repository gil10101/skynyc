"use client";

/** PROOF section: the derive-and-validate loop. Quality tiles show the latest
 *  fully scored day; unscored renders as "—", never zero — the mart's honesty
 *  gates pass through to the UI untouched. */

import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { AIRPORT_LABEL, AIRPORT_ORDER, usePaletteOrDark } from "@/lib/palette";
import type { EventRow, QualityDay } from "@/lib/types";
import type { Filters } from "@/lib/useFilters";
import { ChartCard, tooltipStyle, useData } from "./ui";

export function QualityTiles({ filters }: { filters: Filters }) {
  const P = usePaletteOrDark();
  const q = useData<{ days: QualityDay[] }>("/v1/quality", { days: 30 }, "quality");
  const days = q.data?.days ?? [];
  const scored = days.filter((d) => d.precision != null);
  const latestDate = scored.length ? scored[scored.length - 1].quality_date : null;
  const latest = scored.filter((d) => d.quality_date === latestDate);

  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {AIRPORT_ORDER.filter((a) => filters.airports.includes(a)).map((icao) => {
        const row = latest.find((d) => d.airport === icao);
        return (
          <div key={icao} className="rounded-xl border border-border bg-surface p-4">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: P.airport[icao] }} />
              <span className="font-mono text-[12.5px] font-bold text-ink">{AIRPORT_LABEL[icao]}</span>
              <span className="ml-auto font-mono text-[10px] text-faint">
                {row ? row.quality_date : "no scored day"}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Metric label="precision" value={row?.precision ?? null} target={0.85} />
              <Metric label="recall" value={row?.recall ?? null} target={0.8} />
            </div>
            <div className="mt-2 font-mono text-[10.5px] text-faint">
              {row ? `${row.arrivals_detected} detected · ${row.arrivals_gt} ground truth` : "awaiting ground truth"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Metric({ label, value, target }: { label: string; value: number | null; target: number }) {
  const P = usePaletteOrDark();
  const met = value != null && value >= target;
  return (
    <div>
      <div className="font-mono text-[10px] tracking-widest text-muted">{label.toUpperCase()}</div>
      <div
        className="mt-1 font-mono text-[26px] font-bold leading-none"
        style={{ color: value == null ? P.faint : met ? P.good : P.warn }}
      >
        {value == null ? "—" : `${(value * 100).toFixed(1)}%`}
      </div>
      <div className="mt-0.5 font-mono text-[9.5px] text-faint">target ≥ {target * 100}%</div>
    </div>
  );
}

export function QualityChart({ filters }: { filters: Filters }) {
  const P = usePaletteOrDark();
  const q = useData<{ days: QualityDay[] }>("/v1/quality", { days: 30 }, "quality");
  const rows = (q.data?.days ?? []).filter((d) => filters.airports.includes(d.airport));
  const byDate = new Map<string, Record<string, number | string | null>>();
  for (const r of rows) {
    const entry = byDate.get(r.quality_date) ?? { date: r.quality_date };
    entry[`${r.airport}_p`] = r.precision;
    entry[`${r.airport}_r`] = r.recall;
    byDate.set(r.quality_date, entry);
  }
  const data = [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  return (
    <ChartCard
      title="Precision & recall by day"
      sub="solid = precision, dashed = recall · gaps are unscored days, not zeros"
      state={q.state}
    >
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 4, right: 8, left: -14, bottom: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="date" />
          <YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
          <Tooltip {...tooltipStyle(P)} formatter={(v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`)} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <ReferenceLine y={0.85} stroke={P.neutral} strokeDasharray="2 4" />
          <ReferenceLine y={0.8} stroke={P.faint} strokeDasharray="2 4" />
          {AIRPORT_ORDER.filter((a) => filters.airports.includes(a)).map((a) => (
            // name feeds both legend and tooltip — without it the tooltip
            // shows the raw dataKey ("KJFK_p").
            <Line key={`${a}_p`} dataKey={`${a}_p`} name={`${AIRPORT_LABEL[a]} precision`}
              stroke={P.airport[a]} strokeWidth={2} connectNulls={false} />
          ))}
          {AIRPORT_ORDER.filter((a) => filters.airports.includes(a)).map((a) => (
            <Line key={`${a}_r`} dataKey={`${a}_r`} name={`${AIRPORT_LABEL[a]} recall`}
              stroke={P.airport[a]} strokeWidth={1.25} strokeDasharray="4 4" connectNulls={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}



export function EventsTable({ filters }: { filters: Filters }) {
  const P = usePaletteOrDark();
  const q = useData<{ events: EventRow[] }>("/v1/events", { limit: 25 }, undefined);
  const rows = (q.data?.events ?? []).filter((e) => filters.airports.includes(e.airport));
  return (
    <ChartCard
      title="Latest derived events"
      sub="the raw product of the detection stream, newest first"
      state={q.state}
      height={300}
    >
      <div className="h-full overflow-y-auto">
        <table className="w-full font-mono text-[11.5px]">
          <thead className="sticky top-0 bg-surface text-left text-[10px] tracking-wider text-muted">
            <tr>
              <th className="pb-1.5 pr-3">TIME UTC</th>
              <th className="pb-1.5 pr-3">TYPE</th>
              <th className="pb-1.5 pr-3">AIRPORT</th>
              <th className="pb-1.5 pr-3">FLIGHT</th>
              <th className="pb-1.5">DETAIL</th>
            </tr>
          </thead>
          <tbody className="text-ink-2">
            {rows.map((e) => (
              <tr key={e.event_id} className="border-t border-border/60">
                <td className="py-1.5 pr-3 text-muted">
                  {new Date(e.event_ts).toISOString().slice(5, 16).replace("T", " ")}
                </td>
                <td className="py-1.5 pr-3">
                  <span style={{ color: P.eventType[e.event_type] }}>{e.event_type}</span>
                </td>
                <td className="py-1.5 pr-3">
                  <span className="flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: P.airport[e.airport] }} />
                    {AIRPORT_LABEL[e.airport]}
                  </span>
                </td>
                <td className="py-1.5 pr-3">{e.callsign ?? e.icao24}</td>
                <td className="py-1.5 text-muted">{detail(e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartCard>
  );
}

function detail(e: EventRow): string {
  const d = e.details ?? {};
  if (e.event_type === "arrival")
    return d["confirmation"] === "coverage_loss"
      ? `signal lost at ${d["last_alt_m"] ?? "?"} m`
      : `touchdown, ${d["dist_km"] ?? "?"} km out`;
  if (e.event_type === "holding")
    return `${Math.round((e.duration_s ?? 0) / 60)} min, ${Math.round(Number(d["cum_track_deg"] ?? 0))}° turned`;
  return `${d["gain_m"] ?? "?"} m regained`;
}
