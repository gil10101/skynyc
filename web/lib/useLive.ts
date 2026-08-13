"use client";

/** The page's heartbeat (spec §4): SSE → REST polling → frozen blob capture.
 *  `tick` increments on every applied snapshot so UI elements can breathe in
 *  sync with the actual data — the signature interaction. */

import { useEffect, useRef, useState } from "react";
import { API_BASE, BLOB_BASE } from "./api";
import type { LiveMode, Snapshot } from "./types";

const POLL_MS = 15_000;
const SSE_STALL_MS = 35_000; // ~3 missed ticks -> demote to polling

export interface Live {
  snap: Snapshot | null;
  mode: LiveMode;
  tick: number;
  lastAt: Date | null;
}

export function useLive(): Live {
  const [state, setState] = useState<Live>({ snap: null, mode: "live", tick: 0, lastAt: null });
  const failures = useRef(0);

  useEffect(() => {
    let source: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let stallTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectDelay = 1000;
    let disposed = false;

    const apply = (snap: Snapshot, mode: LiveMode) => {
      if (disposed) return;
      setState((prev) => ({ snap, mode, tick: prev.tick + 1, lastAt: new Date() }));
    };

    const armStallTimer = () => {
      if (stallTimer) clearTimeout(stallTimer);
      stallTimer = setTimeout(() => {
        source?.close();
        startPolling();
      }, SSE_STALL_MS);
    };

    const startSse = () => {
      if (disposed) return;
      source = new EventSource(`${API_BASE}/v1/stream`);
      source.addEventListener("snap", (event) => {
        reconnectDelay = 1000;
        failures.current = 0;
        armStallTimer();
        apply(JSON.parse((event as MessageEvent).data) as Snapshot, "live");
      });
      source.onerror = () => {
        source?.close();
        failures.current += 1;
        if (failures.current >= 2) startPolling();
        else {
          setTimeout(startSse, reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, 30_000);
        }
      };
      armStallTimer();
    };

    const startPolling = () => {
      if (disposed || pollTimer) return;
      if (stallTimer) clearTimeout(stallTimer);
      const poll = async () => {
        try {
          const response = await fetch(`${API_BASE}/v1/snapshot`, { signal: AbortSignal.timeout(4000) });
          if (!response.ok) throw new Error(String(response.status));
          apply((await response.json()) as Snapshot, "polling");
          failures.current = 0;
        } catch {
          failures.current += 1;
          if (failures.current >= 2) await freeze();
        }
      };
      void poll();
      pollTimer = setInterval(poll, POLL_MS);
    };

    const freeze = async () => {
      if (!BLOB_BASE) return;
      try {
        const response = await fetch(`${BLOB_BASE}/live.json`, { signal: AbortSignal.timeout(8000) });
        if (!response.ok) return;
        const snap = (await response.json()) as Snapshot;
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
        apply(snap, "frozen");
      } catch {
        /* keep last state; polling keeps trying */
      }
    };

    startSse();
    return () => {
      disposed = true;
      source?.close();
      if (pollTimer) clearInterval(pollTimer);
      if (stallTimer) clearTimeout(stallTimer);
    };
  }, []);

  return state;
}
