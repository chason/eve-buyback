/** Minted-session journeys: the app behind login, over real HTTP with the real
 * HttpOnly session cookie — the seam the unit suites can't reach (ADR-0046). */
import { expect, test } from "../support/fixtures"

test("member: sees the member nav, their identity, and empty history", async ({
  page,
  loginAs,
}) => {
  await loginAs("member")
  await page.goto("/")
  await expect(page.getByRole("link", { name: "Appraise" })).toBeVisible()
  // Members don't get manager tools.
  await expect(page.getByRole("link", { name: "Config" })).toHaveCount(0)
  // The signed-in character shows as the HUD identity tag.
  await expect(page.locator(".hud-user")).toHaveText("Miko Ren")

  await page.goto("/appraisals")
  await expect(
    page.getByRole("heading", { name: "Appraisals" }),
  ).toBeVisible()
  await expect(page.getByText("No appraisals yet")).toBeVisible()
})

test("CEO: sees manager tools and the config page loads with defaults", async ({
  page,
  loginAs,
}) => {
  await loginAs("ceo")
  await page.goto("/")
  for (const name of ["Config", "Rules", "Locations", "Managers"]) {
    await expect(page.getByRole("link", { name })).toBeVisible()
  }

  // The config page lazily creates the corp's default config on first read —
  // a real DB write through the whole stack.
  await page.goto("/config")
  await expect(
    page.getByRole("heading", { name: "Buyback config" }),
  ).toBeVisible()
  await expect(page.getByLabel("Default percentage")).toHaveValue("90")
})

test("log out ends the session for real", async ({ page, loginAs }) => {
  await loginAs("member")
  await page.goto("/")
  await page.getByRole("button", { name: "Log out" }).click()
  await expect(
    page.getByRole("heading", { name: "Sign in to your corp buyback" }),
  ).toBeVisible()
  // The cleared cookie means the API also rejects us now.
  const res = await page.request.get("/api/v1/auth/me")
  expect(res.status()).toBe(401)
})
