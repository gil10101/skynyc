"use client";

/** Top strip: identity, the breathing LIVE indicator (re-keyed per tick so the
 *  CSS pulse fires exactly when data arrives), and the honesty banner when the
 *  pipeline degrades (spec §4). */

import { useEffect, useState } from "react";
import type { Live } from "@/lib/useLive";

function since(date: Date | null, now: number): string {
  if (!date) return "—";
  const seconds = Math.max(0, Math.round((now - date.getTime()) / 1000));
  if (seconds < 90) return `${seconds}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

export default function StatusBar({ live, paused, onPause }: {
  live: Live;
  paused: boolean;
  onPause: (p: boolean) => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const freshness = live.snap?.freshness_s ?? null;
  const degraded =
    live.mode === "frozen" || (freshness != null && freshness > 900);
  const delayed = !degraded && freshness != null && freshness > 120;

  const dotColor = degraded ? "#d03b3b" : delayed ? "#fab219" : "#0ca30c";
  const label = degraded ? "OFFLINE" : delayed ? "DELAYED" : live.mode === "polling" ? "LIVE·POLL" : "LIVE";

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-page/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-4 py-2.5">
        <div className="font-mono text-[13px] font-bold tracking-[0.18em] text-ink">SKYNYC</div>
        <div className="hidden text-[12px] text-muted sm:block">
          weather impact on NYC arrivals, measured live
        </div>
        <div className="ml-auto flex items-center gap-3">
          <span className="flex items-center gap-2 font-mono text-[11.5px] text-ink-2">
            <span key={live.tick} className="pulse-tick relative flex h-2.5 w-2.5">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: dotColor }} />
            </span>
            {label}
            <span className="text-faint">·</span>
            <span className="text-muted">updated {since(live.lastAt, now)}</span>
          </span>
          <button
            onClick={() => onPause(!paused)}
            className="rounded-md border border-border px-2 py-1 font-mono text-[10.5px] text-ink-2 hover:border-border-2"
            aria-pressed={paused}
          >
            {paused ? "resume motion" : "pause motion"}
          </button>
          <a
            href="https://github.com/gil10101/skynyc"
            className="hidden rounded-md border border-border px-2 py-1 font-mono text-[10.5px] text-ink-2 hover:border-border-2 sm:block"
          >
            source
          </a>
        </div>
      </div>
      {degraded && (
        <div className="border-t border-bad/40 bg-bad/10 px-4 py-1.5 text-center text-[12px] text-ink-2">
          Pipeline offline since{" "}
          <span className="font-mono">
            {live.snap ? new Date(live.snap.generated_at).toLocaleString() : "—"}
          </span>{" "}
          — showing the last capture. Historical sections below remain complete.
        </div>
      )}
    </header>
  );
}
