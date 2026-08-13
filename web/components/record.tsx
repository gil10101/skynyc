"use client";

/** RECORD section: the 38-year gold mart. Year-range slider drives the
 *  staircase re-query; the rest are canned aggregates that survive the droplet. */

import { useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { AIRPORT, BTS_ORDER, CATEGORY, CATEGORY_ORDER } from "@/lib/palette";
import type {
  CauseYear, MonthlyPoint, SeasonMonth, StaircaseCell, WorstDay,
} from "@/lib/types";
import { ChartCard, TOOLTIP_STYLE, useData } from "./ui";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function Staircase({ frozen }: { frozen: boolean }) {
  const [range, setRange] = useState<[number, number]>([1987, 2026]);
  const q = useData<{ cells: StaircaseCell[] }>(
    "/v1/history/staircase", { from_year: range[0], to_year: range[1] }, "staircase",
  );
  const cells = q.data?.cells ?? [];
  const data = CATEGORY_ORDER.map((cat) => {
    const entry: Record<string, number | string> = { category: cat };
    for (const airport of BTS_ORDER) {
      const cell = cells.find((c) => c.airport === airport && c.worst_category === cat);
      if (cell) entry[airport] = cell.avg_delay_min;
    }
    return entry;
  });
  return (
    <ChartCard
      title="Average arrival delay by flight category"
      sub={`average delay minutes per day · ${range[0]}–${range[1]}`}
      state={q.state}
      right={
        <YearRange value={range} onChange={setRange} disabled={frozen} />
      }
    >
      <ResponsiveContainer>
        <BarChart data={data} barCategoryGap="22%" margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="category" tick={{ fontSize: 12, fontWeight: 700 }} />
          <YAxis width={44} tickFormatter={(v) => String(Math.round(Number(v)))} />
          <Tooltip {...TOOLTIP_STYLE} formatter={(v) => [`${v} min`]} />
          <Legend wrapperStyle={{ fontSize: 11.5 }} />
          {BTS_ORDER.map((airport) => (
            <Bar key={airport} dataKey={airport} fill={AIRPORT[airport]} radius={[3, 3, 0, 0]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function YearRange({ value, onChange, disabled }: {
  value: [number, number];
  onChange: (v: [number, number]) => void;
  disabled: boolean;
}) {
  const [lo, hi] = value;
  return (
    <div
      className={`flex items-center gap-2 font-mono text-[10.5px] text-muted ${disabled ? "opacity-50" : ""}`}
      title={disabled ? "live API offline — full-history capture shown" : undefined}
    >
      <span>{lo}</span>
      <input
        type="range" min={1987} max={hi - 5} value={lo} disabled={disabled}
        onChange={(e) => onChange([Number(e.target.value), hi])}
        className="w-20 accent-[#3987e5]"
        aria-label="start year"
      />
      <input
        type="range" min={lo + 5} max={2026} value={hi} disabled={disabled}
        onChange={(e) => onChange([lo, Number(e.target.value)])}
        className="w-20 accent-[#3987e5]"
        aria-label="end year"
      />
      <span>{hi}</span>
    </div>
  );
}

export function Causes() {
  const q = useData<{ years: CauseYear[] }>("/v1/history/causes", {}, "causes");
  return (
    <ChartCard
      title="Weather vs NAS delay by year"
      sub="thousand hours of arrival delay per year · attribution exists from June 2003"
      state={q.state}
    >
      <ResponsiveContainer>
        <AreaChart data={q.data?.years ?? []} margin={{ top: 4, right: 8, left: -14, bottom: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="year" />
          <YAxis width={44} tickFormatter={(v) => String(Math.round(Number(v)))} />
          <Tooltip {...TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ fontSize: 11.5 }} />
          <Area dataKey="nas_khr" name="NAS" stroke="#d95926" fill="#d9592635" strokeWidth={2} />
          <Area dataKey="weather_khr" name="weather" stroke="#e34948" fill="#e3494835" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function Seasonality() {
  const q = useData<{ months: SeasonMonth[] }>("/v1/history/seasonality", {}, "seasonality");
  const rows = q.data?.months ?? [];
  const data = MONTHS.map((label, i) => {
    const entry: Record<string, number | string> = { month: label };
    for (const airport of BTS_ORDER) {
      const r = rows.find((x) => x.airport === airport && x.month === i + 1);
      if (r) entry[airport] = r.wx_cancelled;
    }
    return entry;
  });
  return (
    <ChartCard
      title="Weather cancellations by month"
      sub="38-year totals — winter and convective-summer seasonality"
      state={q.state}
    >
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 11 }} />
          <YAxis />
          <Tooltip {...TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ fontSize: 11.5 }} />
          {BTS_ORDER.map((airport) => (
            <Bar key={airport} dataKey={airport} stackId="s" fill={AIRPORT[airport]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function WorstDays() {
  const [metric, setMetric] = useState<"wx_cancelled" | "wx_delay">("wx_cancelled");
  const q = useData<{ days: WorstDay[] }>("/v1/history/worst", { limit: 10, metric }, "worst");
  return (
    <ChartCard
      title="Worst weather days on record"
      sub="Sandy, the 2018 blizzards, February 2026"
      state={q.state}
      height={318}
      right={
        <div className="flex overflow-hidden rounded-md border border-border font-mono text-[10px]">
          {(["wx_cancelled", "wx_delay"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMetric(m)}
              className={`px-2 py-1 ${metric === m ? "bg-surface-2 text-ink" : "text-muted"}`}
              aria-pressed={metric === m}
            >
              {m === "wx_cancelled" ? "cancellations" : "delay hours"}
            </button>
          ))}
        </div>
      }
    >
      <div className="h-full overflow-y-auto">
        <table className="w-full font-mono text-[11.5px]">
          <thead className="sticky top-0 bg-surface text-left text-[10px] tracking-wider text-muted">
            <tr>
              <th className="pb-1.5 pr-3">DATE</th>
              <th className="pb-1.5 pr-3">AIRPORT</th>
              <th className="pb-1.5 pr-3">CATEGORY</th>
              <th className="pb-1.5 pr-3 text-right">SCHEDULED</th>
              <th className="pb-1.5 pr-3 text-right">WX CANCELLED</th>
              <th className="pb-1.5 text-right">WX DELAY H</th>
            </tr>
          </thead>
          <tbody className="text-ink-2">
            {(q.data?.days ?? []).map((d, i) => (
              <tr key={i} className="border-t border-border/60">
                <td className="py-1.5 pr-3">{d.flight_date}</td>
                <td className="py-1.5 pr-3">
                  <span className="flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: AIRPORT[d.airport] }} />
                    {d.airport}
                  </span>
                </td>
                <td className="py-1.5 pr-3">
                  <span style={{ color: d.worst_category ? CATEGORY[d.worst_category] : "#7d8894" }}>
                    {d.worst_category ?? "—"}
                  </span>
                </td>
                <td className="py-1.5 pr-3 text-right">{d.arrivals_scheduled ?? "—"}</td>
                <td className="py-1.5 pr-3 text-right">{d.cancelled_weather ?? "—"}</td>
                <td className="py-1.5 text-right">{d.wx_delay_hours ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartCard>
  );
}

export function Monthly() {
  const q = useData<{ months: MonthlyPoint[] }>("/v1/history/monthly", {}, "monthly");
  const rows = q.data?.months ?? [];
  const byMonth = new Map<string, Record<string, number | string>>();
  for (const r of rows) {
    const entry = byMonth.get(r.month) ?? { month: r.month };
    entry[r.worst_category] = r.avg_delay_min;
    byMonth.set(r.month, entry);
  }
  const data = [...byMonth.values()].sort((a, b) => String(a.month).localeCompare(String(b.month)));
  return (
    <ChartCard
      title="Monthly arrival delay by flight category"
      sub="average delay minutes · 2000–present (BTS publishes nothing for the 1990s)"
      state={q.state}
    >
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 4, right: 8, left: -14, bottom: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis
            dataKey="month"
            tickFormatter={(v) => String(v).slice(0, 4)}
            minTickGap={48}
          />
          <YAxis width={44} tickFormatter={(v) => String(Math.round(Number(v)))} />
          <Tooltip {...TOOLTIP_STYLE} labelFormatter={(v) => String(v).slice(0, 7)} />
          <Legend wrapperStyle={{ fontSize: 11.5 }} />
          {CATEGORY_ORDER.map((cat) => (
            <Line
              key={cat} dataKey={cat} stroke={CATEGORY[cat]} dot={false}
              strokeWidth={cat === "LIFR" ? 2.25 : 1.5} connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
