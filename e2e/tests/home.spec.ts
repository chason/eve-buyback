/** Logged-out smoke: the served SPA renders, auth gates hold over real HTTP. */
import { expect, test } from "../support/fixtures"

test("logged out: login card shows, member nav does not", async ({ page }) => {
  await page.goto("/")
  await expect(
    page.getByRole("heading", { name: "Sign in to your corp buyback" }),
  ).toBeVisible()
  await expect(page.getByRole("link", { name: "Appraise" })).toHaveCount(0)
})

test("logged out: a deep link into the app bounces to the login card", async ({
  page,
}) => {
  await page.goto("/appraisals")
  await expect(
    page.getByRole("heading", { name: "Sign in to your corp buyback" }),
  ).toBeVisible()
})

test("state-changing API calls require the CSRF header (ADR-0017)", async ({
  page,
}) => {
  // Real-HTTP middleware check: no X-Buyback-CSRF header → rejected before auth.
  const res = await page.request.post("/api/v1/corporations")
  expect(res.status()).toBe(403)
})
