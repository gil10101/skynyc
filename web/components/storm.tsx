"use client";

/** EVENT section: the August 20 storm, replayed from the archived record.
 *  The window is static by design — it is the run's clearest cause-and-effect
 *  exhibit, so it ships with the page instead of depending on any store. */

import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ReferenceArea,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { AIRPORT_LABEL, AIRPORT_ORDER, usePaletteOrDark } from "@/lib/palette";
import type { Filters } from "@/lib/useFilters";
import storm from "@/lib/storm-2026-08-20.json";
import { ChartCard, tooltipStyle } from "./ui";

interface StormArrival { bucket_ts: string; airport: string; arrivals: number }
interface StormWind {
  obs_ts: string; station: string;
  wind_speed_kmh: number | null; wind_gust_kmh: number | null;
  visibility_m: number | null; flight_category: string | null;
}

const WINDOW_FROM = Date.parse(storm.window.from);
const WINDOW_TO = Date.parse(storm.window.to);

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });

/** Nearest 15-min bucket at or after the given instant — ReferenceArea on a
 *  categorical axis must name values that exist in the data. */
const bucketAfter = (iso: string) => {
  const t = new Date(iso);
  t.setUTCMinutes(Math.ceil(t.getUTCMinutes() / 15) * 15, 0, 0);
  return t.toISOString().replace(".000", "");
};

function arrivalsData() {
  const byTs = new Map<string, Record<string, number | string>>();
  for (let t = WINDOW_FROM; t < WINDOW_TO; t += 900_000) {
    const ts = new Date(t).toISOString().replace(".000", "");
    byTs.set(ts, { ts });
  }
  for (const r of storm.arrivals as StormArrival[]) {
    const entry = byTs.get(r.bucket_ts);
    if (entry) entry[r.airport] = r.arrivals;
  }
  return [...byTs.values()];
}

function windData() {
  const byTs = new Map<string, Record<string, number | string>>();
  for (const r of storm.wind as StormWind[]) {
    const entry = byTs.get(r.obs_ts) ?? { ts: r.obs_ts };
    if (r.wind_speed_kmh != null) entry[r.station] = r.wind_speed_kmh;
    if (r.wind_gust_kmh != null) entry[`${r.station}_gust`] = r.wind_gust_kmh;
    byTs.set(r.obs_ts, entry);
  }
  return [...byTs.values()].sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
}

function Stat({ value, label, detail }: { value: string; label: string; detail: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="font-mono text-[26px] font-bold leading-none text-ink">{value}</div>
      <div className="mt-1.5 font-mono text-[10px] tracking-widest text-muted">{label}</div>
      <div className="mt-1 text-[11.5px] text-faint">{detail}</div>
    </div>
  );
}

export default function StormSection({ filters }: { filters: Filters }) {
  const P = usePaletteOrDark();
  const airports = AIRPORT_ORDER.filter((a) => filters.airports.includes(a));
  const warnFrom = bucketAfter(storm.window.warning_from);
  const warnTo = bucketAfter(storm.window.warning_to);
  // The wind axis is categorical over raw observation timestamps, so the
  // warning band must borrow the nearest timestamps that actually exist.
  const windRows = windData();
  const windTs = windRows.map((r) => String(r.ts));
  const windWarnFrom = windTs.find((t) => t >= storm.window.warning_from) ?? windTs[0];
  const windWarnTo = [...windTs].reverse().find((t) => t <= storm.window.warning_to) ?? windTs[windTs.length - 1];
  const band = { fill: P.warn, fillOpacity: 0.09, stroke: P.warn, strokeOpacity: 0.35, strokeDasharray: "3 4" };

  return (
    <>
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Stat value="−74%" label="ARRIVAL RATE" detail="103/hr at 18:00 UTC to 27/hr at 22:00" />
        <Stat value="805 m" label="VISIBILITY" detail="LaGuardia, LIFR, 41.9 mm/hr rain" />
        <Stat value="78 km/h" label="PEAK GUST" detail="Newark, strongest of the run" />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Arrivals per 15 minutes"
          sub="shaded band = Flash Flood Warning in effect, 21:39–00:15 UTC"
        >
          <ResponsiveContainer>
            <ComposedChart data={arrivalsData()} barCategoryGap={1} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="ts" tickFormatter={hhmm} minTickGap={40} />
              <YAxis allowDecimals={false} />
              <Tooltip {...tooltipStyle(P)} labelFormatter={(v) => new Date(String(v)).toLocaleString()} />
              <Legend formatter={(v) => AIRPORT_LABEL[v] ?? v} wrapperStyle={{ fontSize: 11.5 }} />
              <ReferenceArea x1={warnFrom} x2={warnTo} {...band} />
              {airports.map((a) => (
                <Bar key={a} dataKey={a} stackId="arr" fill={P.airport[a]} />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard
          title="Wind and gusts"
          sub="km/h · sustained lines, gusts dashed · same warning band"
        >
          <ResponsiveContainer>
            <LineChart data={windRows} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="ts" tickFormatter={hhmm} minTickGap={40} />
              <YAxis width={44} tickFormatter={(v) => String(Math.round(Number(v)))} />
              <Tooltip {...tooltipStyle(P)} labelFormatter={(v) => new Date(String(v)).toLocaleString()} />
              <Legend
                formatter={(v: string) =>
                  v.endsWith("_gust") ? `${AIRPORT_LABEL[v.slice(0, 4)]} gust` : AIRPORT_LABEL[v] ?? v}
                wrapperStyle={{ fontSize: 11.5 }}
              />
              <ReferenceArea x1={windWarnFrom} x2={windWarnTo} {...band} />
              {airports.map((a) => (
                <Line key={a} dataKey={a} stroke={P.airport[a]} dot={false} strokeWidth={2} connectNulls />
              ))}
              {airports.map((a) => (
                <Line
                  key={`${a}_gust`} dataKey={`${a}_gust`} stroke={P.airport[a]} strokeDasharray="3 4"
                  dot={{ r: 2, strokeWidth: 0, fill: P.airport[a] }}
                  strokeWidth={1.25} connectNulls strokeOpacity={0.85}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </>
  );
}
