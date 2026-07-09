import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as authApi from "../api/auth"
import * as managersApi from "../api/managers"
import * as rosterApi from "../api/roster"
import * as structuresApi from "../api/structures"
import type { SessionUser } from "../api/types"
import Managers from "./Managers"

vi.mock("../api/auth")
vi.mock("../api/managers")
vi.mock("../api/roster")
vi.mock("../api/structures")

function user(over: Partial<SessionUser> = {}): SessionUser {
  return {
    character_id: 1,
    character_name: "Boss",
    corporation_id: 2,
    corporation_name: "Corp",
    role: "ceo",
    is_director: false,
    corporation_registered: true,
    can_open_contract: false,
    is_app_admin: false,
    ...over,
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Managers />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Managers", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(authApi.getMe).mockResolvedValue(user())
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: true,
      expired: false,
      character_name: "Boss",
    })
    vi.mocked(rosterApi.getRosterStatus).mockResolvedValue({
      synced: true,
      synced_at: "2026-06-18T00:00:00Z",
      member_count: 3,
    })
    vi.mocked(managersApi.listManagers).mockResolvedValue([])
  })

  it("blocks a plain manager (non-CEO/Director)", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(
      user({ role: "manager", is_director: false }),
    )
    renderPage()
    expect(
      await screen.findByText(/Only a CEO or Director/i),
    ).toBeInTheDocument()
  })

  it("allows a Director (not a manager) through the gate", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(
      user({ role: "member", is_director: true }),
    )
    renderPage()
    expect(
      await screen.findByLabelText("Search corp members"),
    ).toBeInTheDocument()
  })

  it("shows the roster freshness line when synced", async () => {
    renderPage()
    expect(await screen.findByText(/Roster synced .* 3 members/)).toBeInTheDocument()
  })

  it("searches the roster and grants a manager", async () => {
    const u = userEvent.setup()
    vi.mocked(rosterApi.searchMembers).mockResolvedValue([
      { character_id: 555, name: "Grunt" },
    ])
    vi.mocked(managersApi.addManager).mockResolvedValue({
      character_id: 555,
      character_name: "Grunt",
      granted_by_character_id: 1,
      granted_at: "2026-06-18T00:00:00Z",
    })

    renderPage()
    const input = await screen.findByLabelText("Search corp members")
    await waitFor(() => expect(input).toBeEnabled())
    await u.type(input, "gru")
    // Roster picks are buttons (actions), not anchors (#80).
    await u.click(await screen.findByRole("button", { name: "Grunt" }))
    await waitFor(() =>
      expect(managersApi.addManager).toHaveBeenCalledWith(555),
    )
  })

  it("lists current managers and removes one", async () => {
    const u = userEvent.setup()
    vi.mocked(managersApi.listManagers).mockResolvedValue([
      {
        character_id: 555,
        character_name: "Grunt",
        granted_by_character_id: 1,
        granted_at: "2026-06-18T00:00:00Z",
      },
    ])
    vi.mocked(managersApi.removeManager).mockResolvedValue(undefined)

    renderPage()
    expect(await screen.findByText("Grunt")).toBeInTheDocument()
    // Remove is an action button, not an anchor (#80)…
    await u.click(screen.getByRole("button", { name: "Remove" }))
    // …and it's destructive, so it opens a confirm popup before firing (#36).
    expect(managersApi.removeManager).not.toHaveBeenCalled()
    await u.click(screen.getByRole("button", { name: "Remove manager" }))
    await waitFor(() =>
      expect(managersApi.removeManager).toHaveBeenCalledWith(555),
    )
  })

  it("prompts to connect the token when it isn't connected", async () => {
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: false,
      expired: false,
    })
    renderPage()
    expect(
      await screen.findByText(/Connect corp ESI access/i),
    ).toBeInTheDocument()
    expect(screen.getByLabelText("Search corp members")).toBeDisabled()
  })
})
