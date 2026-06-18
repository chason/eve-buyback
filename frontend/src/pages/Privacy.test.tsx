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

    // No login token is persisted (ADR-0004).
    expect(
      screen.getByText(/do not store your EVE login token/i),
    ).toBeInTheDocument()
    // The one persisted token is encrypted at rest (ADR-0029).
    expect(screen.getByText(/encrypted at rest/i)).toBeInTheDocument()
    // Current ADR-0036: one token per corp, not a transient step-up.
    expect(screen.getByText(/one token per corporation/i)).toBeInTheDocument()
    // The roster snapshot caches only names + ids.
    expect(screen.getByText(/character names and ids only/i)).toBeInTheDocument()
  })

  it("links to the Config page for revoking access", () => {
    renderPrivacy()
    const link = screen.getByRole("link", { name: "Config" })
    expect(link).toHaveAttribute("href", "/config")
  })
})
