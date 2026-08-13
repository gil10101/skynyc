import { expect, test } from "@playwright/test";

// Run against `next start -p 3002` (any API base). Verifies the section
// skeleton, an applied live tick, and the fallback banner path with SSE +
// snapshot + blob all mocked at the network layer.

test("sections render and a mocked tick applies", async ({ page }) => {
  const snap = {
    generated_at: new Date().toISOString(),
    positions: [{ icao24: "abc123", callsign: "TEST1", lat: 40.7, lon: -73.9, alt_m: 900,
      on_ground: false, vel_ms: 80, track_deg: 45, vrate_ms: -2, category: 3, age_s: 5 }],
    conditions: [{ station: "KJFK", obs_ts: new Date().toISOString(), temp_c: 20,
      wind_speed_kmh: 10, wind_gust_kmh: null, wind_dir_deg: 200, visibility_m: 16000,
      ceiling_ft: null, flight_category: "VFR", text_desc: "Clear" }],
    alerts: [],
    freshness_s: 12,
  };
  await page.route("**/v1/stream", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: `event: snap\ndata: ${JSON.stringify(snap)}\n\n`,
    }),
  );
  await page.goto("/");
  await expect(page.getByText("NOW · LIVE OPERATIONS")).toBeVisible();
  await expect(page.getByText("PROOF · THE VALIDATION LOOP")).toBeVisible();
  await expect(page.getByText("RECORD · 38 YEARS OF WEATHER COST")).toBeVisible();
  await expect(page.getByText(/LIVE/).first()).toBeVisible();
  await expect(page.getByText("12s").first()).toBeVisible(); // freshness from the mocked tick
});

test("dead API + blob flips to frozen banner", async ({ page }) => {
  const stale = {
    generated_at: "2026-08-10T00:00:00Z",
    positions: [], conditions: [], alerts: [], freshness_s: 999999,
  };
  await page.route("**/v1/**", (route) => route.abort());
  await page.route("**/live.json", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stale) }),
  );
  await page.addInitScript(() => {
    // make the blob base resolvable in the page bundle
    Object.defineProperty(window, "__blobTest", { value: true });
  });
  await page.goto("/");
  await expect(page.getByText(/Pipeline offline since/)).toBeVisible({ timeout: 45_000 });
});
