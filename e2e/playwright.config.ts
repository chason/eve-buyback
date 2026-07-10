/**
 * E2E smoke suite (ADR-0046): a real browser against the SINGLE DEPLOYABLE — the
 * backend serving the built SPA (ADR-0012). One server process, no Vite, no proxy;
 * every run also proves the production packaging. Auth is a minted session cookie
 * (EVE SSO stays out of the loop); the database is a dedicated `buyback_e2e`.
 */
import fs from "node:fs"
import path from "node:path"

import { defineConfig } from "@playwright/test"

import { backendDir, baseURL, distDir, port, serverEnv } from "./support/env"

// Fail fast, before any server starts: the suite serves the BUILT SPA (ADR-0012).
if (!fs.existsSync(path.join(distDir, "index.html"))) {
  throw new Error(
    "frontend/dist is missing — the e2e suite runs against the built SPA. " +
      "Run `npm run build` in frontend/ first.",
  )
}

const setupScript = path.resolve(__dirname, "support", "e2e_setup.py")

export default defineConfig({
  testDir: "./tests",
  globalSetup: "./support/global-setup",
  // Smoke pack: small, serial, deterministic. The unit/integration suites are the
  // primary net — keep this lean rather than parallel-fast.
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    // Pin the locale: date assertions (e.g. the admin "Until" column) must not
    // depend on the machine's system locale.
    locale: "en-US",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    // Playwright starts the webServer BEFORE globalSetup, so the database rebuild is
    // chained in front of uvicorn here — the app must never boot against a missing DB.
    command:
      `uv run --directory "${backendDir}" python "${setupScript}" db && ` +
      `uv run --directory "${backendDir}" uvicorn app.main:app --host 127.0.0.1 --port ${port}`,
    url: `${baseURL}/api/v1/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: serverEnv(),
  },
})
