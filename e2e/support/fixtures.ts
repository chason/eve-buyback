/**
 * Test fixtures (ADR-0046). `loginAs(persona)` injects a minted, signed session
 * cookie via context.addCookies() — which, unlike page JavaScript, can set the
 * HttpOnly cookie the app uses. EVE SSO never runs in e2e.
 */
import fs from "node:fs"
import path from "node:path"

import { test as base } from "@playwright/test"

import { artifactsDir, baseURL } from "./env"

export type Persona = "ceo" | "member" | "admin"

function sessionCookie(persona: Persona): string {
  const file = path.join(artifactsDir, "sessions.json")
  const sessions = JSON.parse(fs.readFileSync(file, "utf8")) as Record<
    string,
    string
  >
  const cookie = sessions[persona]
  if (!cookie) throw new Error(`no minted session for persona "${persona}"`)
  return cookie
}

export const test = base.extend<{
  loginAs: (persona: Persona) => Promise<void>
}>({
  loginAs: async ({ context }, use) => {
    await use(async (persona) => {
      await context.addCookies([
        {
          name: "buyback_session",
          value: sessionCookie(persona),
          url: baseURL,
          httpOnly: true,
          sameSite: "Lax",
        },
      ])
    })
  },
})

export { expect } from "@playwright/test"
