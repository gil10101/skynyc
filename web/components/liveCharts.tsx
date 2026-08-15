"use client";

/** NOW-section charts: arrivals, airborne delay, wind, arrival-vs-wind scatter.
 *  All filter-aware; airports keep their fixed hues and order. */

import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from "recharts";
import { AIRPORT_LABEL, AIRPORT_ORDER, usePaletteOrDark } from "@/lib/palette";
import type { Filters } from "@/lib/useFilters";
import type { AirbornePoint, ArrivalPoint, ScatterPoint, WindPoint } from "@/lib/types";
import { ChartCard, tooltipStyle, useData } from "./ui";

const shortTime = (iso: string, hours: number) => {
  const d = new Date(iso);
  return hours > 24
    ? d.toLocaleDateString(undefined, { month: "numeric", day: "numeric" })
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
};

function pivot<T extends { airport: string; bucket_ts: string }>(
  rows: T[], value: (r: T) => number,
): Record<string, number | string>[] {
  const byTs = new Map<string, Record<string, number | string>>();
  for (const r of rows) {
    const entry = byTs.get(r.bucket_ts) ?? { ts: r.bucket_ts };
    entry[r.airport] = value(r);
    byTs.set(r.bucket_ts, entry);
  }
  return [...byTs.values()].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
}

/** At 7d the API's quarter-hour grain is 672 buckets — sub-pixel bars that
 *  antialias into a wash. Above 48h, sum into hourly buckets so every bar
 *  keeps visible paint; the card title states the grain it actually shows. */
function rollupHourly(rows: ArrivalPoint[]): ArrivalPoint[] {
  const byKey = new Map<string, ArrivalPoint>();
  for (const r of rows) {
    const d = new Date(r.bucket_ts);
    d.setUTCMinutes(0, 0, 0);
    const bucket_ts = d.toISOString();
    const prev = byKey.get(`${r.airport}|${bucket_ts}`);
    if (prev) prev.arrivals += r.arrivals;
    else byKey.set(`${r.airport}|${bucket_ts}`, { ...r, bucket_ts });
  }
  return [...byKey.values()];
}

export function ArrivalsChart({ filters }: { filters: Filters }) {
  const P = usePaletteOrDark();
  const airport = filters.airports.join(",");
  const q = useData<{ series: ArrivalPoint[] }>(
    "/v1/arrivals", { hours: filters.hours, airport }, "arrivals",
  );
  const hourly = filters.hours > 48;
  const series = q.data?.series ?? [];
  const data = pivot(hourly ? rollupHourly(series) : series, (r) => r.arrivals);
  return (
    <ChartCard
      title={hourly ? "Arrivals per hour" : "Arrivals per 15 minutes"}
      sub="derived from ADS-B by the detection stream — no arrivals feed exists"
      state={q.state}
    >
      <ResponsiveContainer>
        <BarChart data={data} barCategoryGap={1} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="ts" tickFormatter={(v) => shortTime(v, filters.hours)} minTickGap={40} />
          <YAxis allowDecimals={false} />
          <Tooltip {...tooltipStyle(P)} labelFormatter={(v) => new Date(String(v)).toLocaleString()} />
          <Legend formatter={(v) => AIRPORT_LABEL[v] ?? v} wrapperStyle={{ fontSize: 11.5 }} />
          {AIRPORT_ORDER.filter((a) => filters.airports.includes(a)).map((a) => (
            <Bar key={a} dataKey={a} stackId="arr" fill={P.airport[a]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function AirborneChart({ filters }: { filters: Filters }) {
  const P = usePaletteOrDark();
  const airport = filters.airports.join(",");
  const q = useData<{ series: AirbornePoint[] }>(
    "/v1/airborne", { hours: filters.hours, airport }, "airborne",
  );
  const rows = q.data?.series ?? [];
  const data = pivot(rows, (r) => r.holding_min);
  const goArounds = rows.filter((r) => r.go_arounds > 0);
  for (const entry of data) {
    const g = goArounds.filter((r) => r.bucket_ts === entry.ts)
      .reduce((n, r) => n + r.go_arounds, 0);
    if (g > 0) entry.go_arounds = g;
  }
  return (
    <ChartCard
      title="Airborne delay"
      sub="holding minutes per hour by airport · dots mark go-arounds"
      state={q.state}
    >
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="ts" tickFormatter={(v) => shortTime(v, filters.hours)} minTickGap={40} />
          <YAxis allowDecimals={false} />
          <Tooltip {...tooltipStyle(P)} labelFormatter={(v) => new Date(String(v)).toLocaleString()} />
          <Legend formatter={(v) => (v === "go_arounds" ? "go-arounds" : AIRPORT_LABEL[v] ?? v)} wrapperStyle={{ fontSize: 11.5 }} />
          {AIRPORT_ORDER.filter((a) => filters.airports.includes(a)).map((a) => (
            <Bar key={a} dataKey={a} stackId="hold" fill={P.airport[a]} />
          ))}
          <Scatter dataKey="go_arounds" fill={P.eventType.go_around} shape="circle" />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function WindChart({ filters }: { filters: Filters }) {
  const P = usePaletteOrDark();
  const q = useData<{ series: WindPoint[] }>("/v1/wind", { hours: filters.hours }, "wind");
  const rows = (q.data?.series ?? []).filter((r) => filters.airports.includes(r.station));
  const byTs = new Map<string, Record<string, number | string>>();
  for (const r of rows) {
    const entry = byTs.get(r.obs_ts) ?? { ts: r.obs_ts };
    if (r.wind_speed_kmh != null) entry[r.station] = r.wind_speed_kmh;
    if (r.wind_gust_kmh != null) entry[`${r.station}_gust`] = r.wind_gust_kmh;
    byTs.set(r.obs_ts, entry);
  }
  const data = [...byTs.values()].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  return (
    <ChartCard title="Wind by station" sub="km/h · sustained lines, gusts dashed" state={q.state}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="ts" tickFormatter={(v) => shortTime(v, filters.hours)} minTickGap={40} />
          <YAxis width={44} tickFormatter={(v) => String(Math.round(Number(v)))} />
          <Tooltip {...tooltipStyle(P)} labelFormatter={(v) => new Date(String(v)).toLocaleString()} />
          <Legend
            formatter={(v: string) =>
              v.endsWith("_gust") ? `${AIRPORT_LABEL[v.slice(0, 4)]} gust` : AIRPORT_LABEL[v] ?? v}
            wrapperStyle={{ fontSize: 11.5 }}
          />
          {AIRPORT_ORDER.filter((a) => filters.airports.includes(a)).map((a) => (
            <Line key={a} dataKey={a} stroke={P.airport[a]} dot={false} strokeWidth={2} connectNulls />
          ))}
          {AIRPORT_ORDER.filter((a) => filters.airports.includes(a)).map((a) => (
            <Line
              key={`${a}_gust`} dataKey={`${a}_gust`} stroke={P.airport[a]} strokeDasharray="3 4"
              // Gusts are reported only while gusting, so the series is sparse;
              // a bare dashed line disappears at 7d density. Dots mark the
              // actual observations, the dashes only bridge them.
              dot={{ r: 2.5, strokeWidth: 0, fill: P.airport[a] }}
              strokeWidth={1.5} connectNulls strokeOpacity={0.85}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function WindScatter({ filters }: { filters: Filters }) {
  const P = usePaletteOrDark();
  const q = useData<{ points: ScatterPoint[] }>("/v1/scatter", { days: 7 }, undefined);
  const points = (q.data?.points ?? []).filter((p) => filters.airports.includes(p.airport));
  return (
    <ChartCard
      title="Arrival rate vs effective wind"
      sub="hourly points, last 7 days · color = flight category at that hour"
      state={q.state}
    >
      <ResponsiveContainer>
        <ScatterChart margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid />
          <XAxis dataKey="effective_wind_kmh" type="number" name="wind" unit=" km/h" />
          <YAxis dataKey="arrivals_detected" type="number" name="arrivals/h" allowDecimals={false} />
          <Tooltip
            {...tooltipStyle(P)}
            // scatter series has no fill (Cells carry it), so recharts' default
            // item color falls back to #000 — invisible on the dark tooltip
            itemStyle={{ padding: 0, color: P.tooltipText }}
            formatter={(value, name) => [String(value), name === "arrivals_detected" ? "arrivals/h" : "wind km/h"]}
          />
          <Scatter data={points}>
            {points.map((p, i) => (
              <Cell key={i} fill={p.flight_category ? (P.category[p.flight_category] ?? P.neutral) : P.neutral} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
