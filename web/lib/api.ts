/** Fetch layer with the degradation contract (spec §4):
 *  API first (2 s timeout, one retry) → Vercel Blob canned slice → throw.
 *  Callers learn which source answered so the UI can label honesty. */

import type { DataSource } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
export const BLOB_BASE = process.env.NEXT_PUBLIC_BLOB_BASE ?? "";

async function fetchJson<T>(url: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`${response.status} ${url}`);
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export interface Sourced<T> {
  data: T;
  source: DataSource;
}

/** history.json shape: { generated_at, data: { [blobKey]: payload | null } } */
interface HistoryBundle {
  generated_at: string;
  data: Record<string, unknown>;
}

let bundleCache: Promise<HistoryBundle> | null = null;
function historyBundle(): Promise<HistoryBundle> {
  bundleCache ??= fetchJson<HistoryBundle>(`${BLOB_BASE}/history.json`, 8000);
  return bundleCache;
}

/**
 * GET an API path; on failure fall back to the named slice of the blob
 * history bundle (blobKey), which carries canned default-timeframe data.
 * Paths with non-default params have no blob equivalent — they reject once
 * the API is down, and callers keep the last good state instead.
 */
export async function get<T>(
  path: string,
  params: Record<string, string | number | undefined> = {},
  blobKey?: string,
): Promise<Sourced<T>> {
  const query = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&");
  const url = `${API_BASE}${path}${query ? `?${query}` : ""}`;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      return { data: await fetchJson<T>(url, 2000 + attempt * 2000), source: "api" };
    } catch {
      /* retry once, then blob */
    }
  }
  if (blobKey && BLOB_BASE) {
    const bundle = await historyBundle();
    const slice = bundle.data[blobKey];
    if (slice != null) return { data: slice as T, source: "blob" };
  }
  throw new Error(`unreachable: ${path}`);
}
