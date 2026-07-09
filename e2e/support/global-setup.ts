/**
 * Global setup (ADR-0046): mint the persona session cookies. Runs a Python helper
 * inside the backend's environment so signing is the app's own (same itsdangerous
 * signer, same secret) — never reimplemented in Node.
 *
 * Note: Playwright starts the webServer BEFORE this runs, so the database rebuild
 * lives in the webServer command (see playwright.config.ts); minting needs no DB.
 */
import { spawnSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"

import { artifactsDir, backendDir, serverEnv } from "./env"

const MARKER = "E2E_SESSIONS_JSON="

export default function globalSetup(): void {
  const script = path.join(__dirname, "e2e_setup.py")
  const result = spawnSync(
    "uv",
    ["run", "--directory", backendDir, "python", script, "mint"],
    {
      env: { ...process.env, ...serverEnv() },
      encoding: "utf8",
      shell: process.platform === "win32", // resolve uv.exe via PATH on Windows
      timeout: 120_000,
    },
  )
  if (result.status !== 0) {
    throw new Error(
      `e2e_setup.py mint failed (exit ${result.status}):\n${result.stdout}\n${result.stderr}`,
    )
  }

  const line = result.stdout.split(/\r?\n/).find((l) => l.startsWith(MARKER))
  if (!line) {
    throw new Error(`e2e_setup.py printed no ${MARKER} line:\n${result.stdout}`)
  }
  fs.mkdirSync(artifactsDir, { recursive: true })
  fs.writeFileSync(
    path.join(artifactsDir, "sessions.json"),
    line.slice(MARKER.length),
  )
  console.log("persona sessions minted")
}
