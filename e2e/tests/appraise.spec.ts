/** The core product flow (#171): paste a haul → priced, saved appraisal — through the
 * whole stack (parse, SDE name resolution, pricing at 90% of the seeded Jita buy
 * percentile, persisted record with a shareable id). No network: the mini-SDE fixture
 * seeds the types and market prices (see e2e_setup.py MINERALS).
 *
 * Math is exact by construction: 1000 Tritanium @ 4.00 + 500 Pyerite @ 8.00, both at
 * 90% → 3,600 + 3,600 = 7,200.00 ISK. */
import { expect, test } from "../support/fixtures"

// Runs as the "hauler" persona — a member of the SECOND corp — so this journey's
// appraisal never leaks into the first corp's history, which the logged-in journeys
// assert is empty. Corp scoping keeps the journeys isolated.
test("member: pastes a haul, gets a priced appraisal with a shareable record", async ({
  page,
  loginAs,
}) => {
  await loginAs("hauler")
  await page.goto("/appraise")

  await page
    .getByLabel("Paste items")
    .fill("Tritanium 1000\nPyerite 500\nSock Puppet 3")
  await page.getByRole("button", { name: "Create appraisal" }).click()

  // Lands on the persisted appraisal at its shareable public id (ADR-0014).
  await page.waitForURL(/\/a\/[A-Za-z0-9_-]{12}$/)
  await expect(page.getByRole("heading", { name: "Appraisal" })).toBeVisible()

  // Priced lines resolved via the SDE fixture, at 90% of the seeded Jita buy. The
  // header total animates through a hidden "typewriter ghost" span, so assert the
  // plain cells: the per-line totals and the contract panel's "I will receive".
  await expect(
    page.getByRole("row", { name: /Tritanium 1,000 buy 90/ }),
  ).toContainText("3,600.00 ISK")
  await expect(
    page.getByRole("cell", { name: "7,200.00 ISK" }),
  ).toBeVisible()

  // The unknown item is kept, visibly rejected (ADR-0008/0021) — not silently dropped.
  await expect(page.getByRole("row", { name: /Sock Puppet/ })).toContainText(
    "Rejected",
  )

  // The record is saved: it shows up in the corp's appraisal history.
  await page.goto("/appraisals")
  await expect(page.getByRole("cell", { name: "7,200.00 ISK" })).toBeVisible()
})
