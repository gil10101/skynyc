import { defineConfig } from "@playwright/test";

// Local smoke only (spec §5): CI runs typecheck + build; this needs a browser.
export default defineConfig({
  testDir: "./tests-e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://localhost:3002",
    viewport: { width: 1440, height: 900 },
    // reuse the browser the plugin cache already has — no download step
    launchOptions: process.env.PW_EXE ? { executablePath: process.env.PW_EXE } : {},
  },
});
