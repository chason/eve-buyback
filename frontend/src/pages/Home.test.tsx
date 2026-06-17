import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as authApi from "../api/auth"
import type { SessionUser } from "../api/types"
import Home from "./Home"

vi.mock("../api/auth")

function user(overrides: Partial<SessionUser> = {}): SessionUser {
  return {
    character_id: 1,
    character_name: "Pilot",
    corporation_id: 2,
    corporation_name: "Test Corp",
    role: "member",
    is_director: false,
    corporation_registered: false,
    ...overrides,
  }
}

function renderHome() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Home — registration", () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it("tells a Director they are now a Buyback Manager after registering", async () => {
    // A Director with no buyback role yet; registering auto-grants them manager.
    vi.mocked(authApi.getMe)
      .mockResolvedValueOnce(user({ role: "member", is_director: true }))
      .mockResolvedValue(
        user({ role: "manager", is_director: true, corporation_registered: true }),
      )
    vi.mocked(authApi.registerCorporation).mockResolvedValue({
      corporation_id: 2,
      name: "Test Corp",
      ceo_character_id: 1,
      registered_by_character_id: 1,
      registered_at: "2026-06-17T00:00:00Z",
    })

    renderHome()

    const button = await screen.findByRole("button", { name: /register test corp/i })
    await userEvent.click(button)

    const status = await screen.findByRole("status")
    expect(status).toHaveTextContent(/now a\s+Buyback Manager/i)
    expect(authApi.registerCorporation).toHaveBeenCalledOnce()
  })

  it("does not show the manager confirmation when a CEO registers", async () => {
    // A CEO stays role "ceo" — there is no manager grant to announce.
    vi.mocked(authApi.getMe)
      .mockResolvedValueOnce(user({ role: "ceo" }))
      .mockResolvedValue(user({ role: "ceo", corporation_registered: true }))
    vi.mocked(authApi.registerCorporation).mockResolvedValue({
      corporation_id: 2,
      name: "Test Corp",
      ceo_character_id: 1,
      registered_by_character_id: 1,
      registered_at: "2026-06-17T00:00:00Z",
    })

    renderHome()

    const button = await screen.findByRole("button", { name: /register test corp/i })
    await userEvent.click(button)

    // The appraisal CTA appears once registered…
    await screen.findByRole("button", { name: /start an appraisal/i })
    // …but no role-change confirmation.
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("shows no confirmation for a manager who is already registered", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(
      user({ role: "manager", is_director: true, corporation_registered: true }),
    )

    renderHome()

    await screen.findByRole("button", { name: /start an appraisal/i })
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })
})
