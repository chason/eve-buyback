/** App-admin access journeys (#148, ADR-0041/0042): the formalized version of the
 * manual verification that shipped the admin UI — gate, grant (perpetual + dated,
 * UTC), and revoke-behind-confirm, in a real browser over real HTTP. */
import { expect, test } from "../support/fixtures"

const CORP_A = { id: 98000001, name: "Deep Space Ventures" }
const CORP_B = { id: 98000002, name: "Jita Freight Collective" }
const CSRF = { "X-Buyback-CSRF": "1" }

test("non-admin: no Admin nav, blocked page, 403 API", async ({
  page,
  loginAs,
}) => {
  await loginAs("member")
  await page.goto("/")
  await expect(page.getByRole("link", { name: "Appraise" })).toBeVisible()
  await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0)

  // The nav hide is cosmetic — the page and the API are the real gates.
  await page.goto("/admin")
  await expect(page.getByText("Only an app admin can manage access")).toBeVisible()
  const res = await page.request.get("/api/v1/admin/access")
  expect(res.status()).toBe(403)
})

test("admin: grants access forever, sees it live", async ({
  page,
  loginAs,
}) => {
  await loginAs("admin")
  await page.goto("/")
  await page.getByRole("link", { name: "Admin" }).click()
  await expect(page.getByRole("heading", { name: "Admin" })).toBeVisible()

  // Both seeded corps list, starting with no access.
  const rowA = page.getByRole("row", { name: new RegExp(CORP_A.name) })
  const rowB = page.getByRole("row", { name: new RegExp(CORP_B.name) })
  await expect(rowA).toContainText("Off")
  await expect(rowB).toContainText("Off")

  // No date picked = access that never expires.
  await rowA.getByRole("button", { name: "Give access" }).click()
  await expect(rowA).toContainText("On")
  await expect(rowA).toContainText("Forever")
  // The grant source shows on hover, not inline.
  await expect(rowA.locator(".access-badge")).toHaveAttribute(
    "title",
    "granted by admin",
  )

  // Cleanup so journeys stay independent (the API is admin-gated + CSRF-checked).
  const res = await page.request.delete(`/api/v1/admin/access/${CORP_A.id}`, {
    headers: CSRF,
  })
  expect(res.status()).toBe(204)
})

test("admin: a dated grant shows the picked day in UTC", async ({
  page,
  loginAs,
}) => {
  await loginAs("admin")
  await page.goto("/admin")

  const rowB = page.getByRole("row", { name: new RegExp(CORP_B.name) })
  await rowB
    .getByLabel(`Access until date for ${CORP_B.name}`)
    .fill("2026-08-09")
  await rowB.getByRole("button", { name: "Give access" }).click()

  await expect(rowB).toContainText("On")
  // Rendered in UTC (EVE time) — the exact regression the manual test caught:
  // a local-time render would show 8/10/2026 east of UTC.
  await expect(rowB).toContainText("8/9/2026")

  const res = await page.request.delete(`/api/v1/admin/access/${CORP_B.id}`, {
    headers: CSRF,
  })
  expect(res.status()).toBe(204)
})

test("admin: revoke sits behind a confirm and turns access off", async ({
  page,
  loginAs,
}) => {
  await loginAs("admin")
  // Arrange through the API (same admin session), assert through the UI.
  const grant = await page.request.put(`/api/v1/admin/access/${CORP_A.id}`, {
    headers: CSRF,
    data: {},
  })
  expect(grant.status()).toBe(200)

  await page.goto("/admin")
  const rowA = page.getByRole("row", { name: new RegExp(CORP_A.name) })
  await expect(rowA).toContainText("On")

  // First click opens the confirm — nothing is revoked yet.
  await rowA.getByRole("button", { name: "Remove", exact: true }).click()
  await expect(rowA).toContainText("On")
  await page.getByRole("button", { name: "Remove access" }).click()

  await expect(rowA).toContainText("Off")
  await expect(rowA).toContainText("—")
})
