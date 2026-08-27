"use client";

/** Shared frame pieces: section header (mono eyebrow = the figure-system voice),
 *  ChartCard with loading/error/frozen states, and the data-fetch hook that
 *  carries the source label through to the UI. */

import { useEffect, useRef, useState } from "react";
import { get, SUNSET_DATE } from "@/lib/api";
import type { DataPalette } from "@/lib/palette";
import type { DataSource } from "@/lib/types";

export function Section({ eyebrow, title, note, children }: {
  eyebrow: string;
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mx-auto max-w-[1400px] px-4 py-10">
      <div className="mb-5">
        <div className="font-mono text-[11px] font-bold tracking-[0.16em] text-muted">{eyebrow}</div>
        <h2 className="mt-1.5 text-[22px] font-bold tracking-tight text-ink">{title}</h2>
        {note && <p className="mt-1 max-w-[90ch] text-[13px] leading-relaxed text-ink-2">{note}</p>}
      </div>
      {children}
    </section>
  );
}

export function ChartCard({ title, sub, right, height = 260, state = "ready", children }: {
  title: string;
  sub?: string;
  right?: React.ReactNode;
  height?: number;
  state?: "ready" | "loading" | "error" | "blob";
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <h3 className="text-[14px] font-semibold text-ink">{title}</h3>
          {sub && <div className="mt-0.5 text-[11.5px] text-muted">{sub}</div>}
        </div>
        <div className="flex items-center gap-2">
          {state === "blob" && (
            SUNSET_DATE ? (
              <span className="rounded-full border border-border bg-secondary/60 px-2 py-0.5 font-mono text-[9.5px] text-muted">
                final capture
              </span>
            ) : (
              <span className="rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 font-mono text-[9.5px] text-warn">
                last capture
              </span>
            )
          )}
          {right}
        </div>
      </div>
      <div className="mt-3" style={{ height }}>
        {state === "loading" && (
          <div className="flex h-full items-center justify-center font-mono text-[11px] text-faint">
            loading…
          </div>
        )}
        {state === "error" && (
          <div className="flex h-full items-center justify-center px-6 text-center font-mono text-[11px] text-faint">
            {SUNSET_DATE
              ? "not in the archived capture — only the default timeframe was preserved"
              : "unavailable — the live API is unreachable and no capture covers this view"}
          </div>
        )}
        {(state === "ready" || state === "blob") && children}
      </div>
    </div>
  );
}

export interface Fetched<T> {
  data: T | null;
  source: DataSource | null;
  state: "loading" | "ready" | "error" | "blob";
}

/** Fetch with re-query on dependency change; keeps last good data during
 *  refetch so filter clicks don't blank panels. */
export function useData<T>(
  path: string,
  params: Record<string, string | number | undefined>,
  blobKey?: string,
): Fetched<T> {
  const [result, setResult] = useState<Fetched<T>>({ data: null, source: null, state: "loading" });
  const key = JSON.stringify([path, params]);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    void (async () => {
      try {
        const { data, source } = await get<T>(path, params, blobKey);
        if (!cancelled && alive.current)
          setResult({ data, source, state: source === "blob" ? "blob" : "ready" });
      } catch {
        if (!cancelled && alive.current)
          setResult((prev) =>
            prev.data ? prev : { data: null, source: null, state: "error" },
          );
      }
    })();
    return () => {
      cancelled = true;
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return result;
}


export function tooltipStyle(p: DataPalette) {
  return {
    contentStyle: {
      background: p.tooltipBg,
      border: `1px solid ${p.tooltipBorder}`,
      borderRadius: 8,
      fontSize: 12,
      color: p.tooltipText,
      fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
    },
    labelStyle: { color: p.tooltipText },
    itemStyle: { padding: 0 },
  } as const;
}
