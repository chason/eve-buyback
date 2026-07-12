import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"

import Privacy from "./Privacy"

function renderPrivacy() {
  return render(
    <MemoryRouter>
      <Privacy />
    </MemoryRouter>,
  )
}

describe("Privacy", () => {
  it("states the key, ADR-accurate data-handling facts (#112)", () => {
    renderPrivacy()

    expect(
      screen.getByRole("heading", { name: /privacy & data use/i }),
    ).toBeInTheDocument()

    // No login token is stored server-side (ADR-0004/0038).
    expect(
      screen.getByText(/store no login token on our servers/i),
    ).toBeInTheDocument()
    // Open in EVE (ADR-0038): the login refresh token rides encrypted in the cookie,
    // used only to open a contract.
    expect(
      screen.getByText(/refresh token is kept encrypted inside that cookie/i),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("heading", { name: /opening a contract in eve/i }),
    ).toBeInTheDocument()
    // Both persisted tokens — the corp token (ADR-0029) and the operator wallet
    // token (ADR-0042) — are encrypted at rest.
    expect(screen.getAllByText(/encrypted at rest/i)).toHaveLength(2)
    // Current ADR-0036: one token per corp, not a transient step-up.
    expect(screen.getByText(/one token per corporation/i)).toBeInTheDocument()
    // The roster snapshot caches only names + ids.
    expect(screen.getByText(/character names and ids only/i)).toBeInTheDocument()
    // Contract tracking (ADR-0037): corp item-exchange contracts only, and only the
    // matched contract id/status/timestamps are kept.
    expect(
      screen.getByRole("heading", { name: /contract tracking/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/contract id, its status, and the issue\/complete timestamps/i),
    ).toBeInTheDocument()
    // Hangar reading (ADR-0044): corp assets scope, marked hangars only, counts only.
    expect(
      screen.getByRole("heading", { name: /reading the buyback hangar/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/item type and quantity counts for those marked hangars/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/never reads members' personal assets/i),
    ).toBeInTheDocument()
    // Shared-link preview (ADR-0040): public, unauthenticated, value + location only.
    expect(
      screen.getByRole("heading", { name: /sharing an appraisal link/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/total ISK value and drop-off location/i),
    ).toBeInTheDocument()
    // Operator wallet (ADR-0042): the app reads only the OPERATOR's own wallet
    // journal for payment matching — never a member's or a corp's wallet.
    expect(
      screen.getByRole("heading", {
        name: /paying for optional add-ons/i,
      }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/that character's own wallet journal/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/never reads any member's or corporation's wallet/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/amount, sender, date, and the transfer reason/i),
    ).toBeInTheDocument()
  })

  it("links to the Config page for revoking access", () => {
    renderPrivacy()
    const link = screen.getByRole("link", { name: "Config" })
    expect(link).toHaveAttribute("href", "/config")
  })

  it("links each cited ADR to its source on GitHub (#112)", () => {
    renderPrivacy()
    const adr = screen.getByRole("link", { name: "ADR-0036" })
    expect(adr).toHaveAttribute(
      "href",
      "https://github.com/chason/eve-buyback/blob/main/docs/adr/0036-corp-roster-manager-designation.md",
    )
    expect(adr).toHaveAttribute("target", "_blank")
  })

  it("links to the open-source repo (#112)", () => {
    renderPrivacy()
    const repo = screen.getByRole("link", { name: /open source on GitHub/i })
    expect(repo).toHaveAttribute("href", "https://github.com/chason/eve-buyback")
  })
})
