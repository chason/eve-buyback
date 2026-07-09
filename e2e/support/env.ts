/**
 * Shared environment resolution for the E2E suite (ADR-0046). One place decides the
 * database URL, session secret, and paths; playwright.config, global setup, and
 * fixtures all import from here so they can never disagree.
 */
import fs from "node:fs"
import path from "node:path"

export const repoRoot = path.resolve(__dirname, "..", "..")
export const backendDir = path.join(repoRoot, "backend")
export const distDir = path.join(repoRoot, "frontend", "dist")
export const artifactsDir = path.resolve(__dirname, "..", ".artifacts")

export const port = 8123
export const baseURL = `http://127.0.0.1:${port}`

// The suite signs its own session cookies (no EVE SSO), so the server and the minting
// script must share this secret. It exists only for local/CI e2e runs.
export const sessionSecret = "e2e-insecure-session-secret"

/** Minimal KEY=VALUE .env reader — real environment variables win over the file. */
function dotenvValue(key: string): string | undefined {
  const file = path.join(backendDir, ".env")
  if (!fs.existsSync(file)) return undefined
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith("#")) continue
    const eq = line.indexOf("=")
    if (eq < 0) continue
    if (line.slice(0, eq).trim() === key) {
      return line
        .slice(eq + 1)
        .trim()
        .replace(/^["']|["']$/g, "")
    }
  }
  return undefined
}

/** The e2e database URL: the configured server (env var, then backend/.env, then the
 * config.py default) with the database name swapped to `buyback_e2e` — mirroring how
 * the pytest suite derives `<name>_test`, so e2e can never touch dev data. */
export function e2eDatabaseUrl(): string {
  const configured =
    process.env.BUYBACK_DATABASE_URL ??
    dotenvValue("BUYBACK_DATABASE_URL") ??
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/buyback"
  return configured.replace(/\/[^/?]+(\?.*)?$/, "/buyback_e2e$1")
}

/** The environment the backend (and the Python setup script) runs with. */
export function serverEnv(): Record<string, string> {
  return {
    BUYBACK_DATABASE_URL: e2eDatabaseUrl(),
    BUYBACK_ENVIRONMENT: "development",
    BUYBACK_SESSION_SECRET: sessionSecret,
    BUYBACK_STATIC_DIR: distDir,
    // Keep the boot quiet and offline: no background ESI/Fuzzwork traffic in e2e.
    BUYBACK_MARKET_BACKGROUND_REFRESH_ENABLED: "false",
    BUYBACK_ROSTER_BACKGROUND_REFRESH_ENABLED: "false",
    BUYBACK_CONTRACTS_BACKGROUND_REFRESH_ENABLED: "false",
  }
}
