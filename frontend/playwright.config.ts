import { defineConfig } from "@playwright/test";

// Browser tests run against the REAL backend and warehouse (no mocks).
// Start the API (python -m salesflow api) and the web app (npm run dev) first,
// with the warehouse loaded. Uses the locally installed Microsoft Edge or
// Chrome, so no browser download is needed: E2E_BROWSER_CHANNEL=chrome to switch.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: process.env.E2E_BROWSER_CHANNEL ?? "msedge",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
});
