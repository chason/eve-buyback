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

  it("orients the manager after registering: live on defaults + setup links", async () => {
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
    // Confirms the corp is live on the default pricing…
    expect(status).toHaveTextContent(/registered/i)
    expect(status).toHaveTextContent(/90% Jita Buy/i)
    // …announces the auto-granted manager role…
    expect(status).toHaveTextContent(/granted\s+Buyback Manager/i)
    // …and links the three setup pages.
    expect(screen.getByRole("link", { name: /^Config$/ })).toHaveAttribute(
      "href",
      "/config",
    )
    expect(screen.getByRole("link", { name: /^Rules$/ })).toHaveAttribute(
      "href",
      "/rules",
    )
    expect(screen.getByRole("link", { name: /^Locations$/ })).toHaveAttribute(
      "href",
      "/locations",
    )
    expect(authApi.registerCorporation).toHaveBeenCalledOnce()
  })

  it("orients a CEO too, but without the manager-grant line", async () => {
    // A CEO stays role "ceo" — gets the orientation, but no role-change to announce.
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

    const status = await screen.findByRole("status")
    expect(status).toHaveTextContent(/90% Jita Buy/i)
    expect(screen.getByRole("link", { name: /^Config$/ })).toBeInTheDocument()
    // No manager-grant line for a CEO.
    expect(status).not.toHaveTextContent(/granted/i)
  })

  it("shows no orientation for a manager who is already registered", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(
      user({ role: "manager", is_director: true, corporation_registered: true }),
    )

    renderHome()

    await screen.findByRole("button", { name: /start an appraisal/i })
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })
})
