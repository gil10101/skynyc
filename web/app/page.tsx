"use client";

/** One page, read like an ops console: SKY (live map) → NOW (operations) →
 *  PROOF (the validation loop) → RECORD (38 years). */

import { Suspense, useState } from "react";
import Controls from "@/components/Controls";
import ConditionsRow from "@/components/ConditionsRow";
import LiveMap from "@/components/LiveMap";
import StatusBar from "@/components/StatusBar";
import { ArrivalsChart, AirborneChart, WindChart, WindScatter } from "@/components/liveCharts";
import { EventsTable, QualityChart, QualityTiles } from "@/components/proof";
import { Causes, Monthly, Seasonality, Staircase, WorstDays } from "@/components/record";
import { Section } from "@/components/ui";
import { useFilters } from "@/lib/useFilters";
import { useLive } from "@/lib/useLive";

export default function Page() {
  return (
    <Suspense>
      <Dashboard />
    </Suspense>
  );
}

function Dashboard() {
  const live = useLive();
  const filters = useFilters();
  const [paused, setPaused] = useState(false);
  const frozen = live.mode === "frozen";

  return (
    <main>
      <StatusBar live={live} paused={paused} onPause={setPaused} />
      <LiveMap live={live} paused={paused} />

      <Section
        eyebrow="NOW · LIVE OPERATIONS"
        title="What the three fields are doing right now"
        note="Positions poll every 30 seconds from ADS-B; weather every 5 minutes from NWS stations on each field. Every panel below derives from those two feeds — nothing is read from a delay product."
      >
        <div className="mb-4">
          <Controls filters={filters} frozen={frozen} />
        </div>
        <ConditionsRow live={live} airports={filters.airports} />
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <ArrivalsChart filters={filters} />
          <AirborneChart filters={filters} />
          <WindChart filters={filters} />
          <WindScatter filters={filters} />
        </div>
      </Section>

      <div className="border-y border-border bg-surface/40">
        <Section
          eyebrow="PROOF · THE VALIDATION LOOP"
          title="The detector grades itself against independent truth, daily"
          note="Arrivals, holding patterns and go-arounds exist in no feed — they are derived from raw telemetry by per-aircraft state machines, then matched every morning against OpenSky's separate arrival records (same aircraft, same airport, within ten minutes). Only arrivals have ground truth; holding and go-arounds are shown but not independently scored. Unscored days display as gaps, never as zeros."
        >
          <QualityTiles filters={filters} />
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <QualityChart filters={filters} />
            <EventsTable filters={filters} />
          </div>
        </Section>
      </div>

      <Section
        eyebrow="RECORD · 38 YEARS OF WEATHER COST"
        title="What weather has cost NYC arrivals since 1987"
        note="181 million federal on-time records, rebuilt into one row per airport-day and joined with historical METAR. Flight categories are derived from each day's worst observed visibility and ceiling. The 1990s are absent because the source publishes no files for that decade."
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <Staircase frozen={frozen} />
          <Causes />
          <Seasonality />
          <WorstDays />
        </div>
        <div className="mt-4">
          <Monthly />
        </div>
      </Section>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-start justify-between gap-6 px-4 py-8 text-[11.5px] text-muted">
          <div className="max-w-[64ch] leading-relaxed">
            <div className="font-mono text-[11px] font-bold tracking-[0.16em] text-ink-2">SKYNYC</div>
            <p className="mt-2">
              Aircraft data © the{" "}
              <a className="text-ink-2 underline decoration-border underline-offset-2" href="https://opensky-network.org">
                OpenSky Network
              </a>{" "}
              (Schäfer et al., <em>Bringing Up OpenSky</em>, IPSN 2014), research use. Weather ©{" "}
              <a className="text-ink-2 underline decoration-border underline-offset-2" href="https://www.weather.gov">
                NOAA / National Weather Service
              </a>
              . Historical on-time performance: U.S. BTS TranStats. Basemap ©{" "}
              OpenStreetMap contributors, © CARTO. Events are <em>derived</em>, and their accuracy
              is published, not claimed.
            </p>
          </div>
          <div className="flex flex-col gap-1.5 font-mono text-[11px]">
            <a className="text-ink-2 hover:text-ink" href="https://github.com/gil10101/skynyc">github.com/gil10101/skynyc</a>
            <a className="text-ink-2 hover:text-ink" href="https://gillu.me">gillu.me</a>
          </div>
        </div>
      </footer>
    </main>
  );
}
