import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as adminApi from "../api/admin"
import * as authApi from "../api/auth"
import type { CorpAccessOut, SessionUser } from "../api/types"
import Admin from "./Admin"

vi.mock("../api/auth")
vi.mock("../api/admin")

function user(over: Partial<SessionUser> = {}): SessionUser {
  return {
    character_id: 1,
    character_name: "Operator",
    corporation_id: 2,
    corporation_name: "Op Corp",
    role: "member",
    is_director: false,
    corporation_registered: true,
    can_open_contract: false,
    is_app_admin: true,
    ...over,
  }
}

function corp(over: Partial<CorpAccessOut> = {}): CorpAccessOut {
  return {
    corporation_id: 98000001,
    corporation_name: "Test Corp",
    active: false,
    source: null,
    granted_at: null,
    expires_at: null,
    granted_by_character_id: null,
    ...over,
  }
}

function renderAdmin() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Admin", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(authApi.getMe).mockResolvedValue(user())
    vi.mocked(adminApi.listCorpAccess).mockResolvedValue([corp()])
  })

  it("blocks a non-admin", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user({ is_app_admin: false }))
    renderAdmin()
    expect(
      await screen.findByText(/Only an app admin/i),
    ).toBeInTheDocument()
    expect(adminApi.listCorpAccess).not.toHaveBeenCalled()
  })

  it("lists corps with plain-English access status", async () => {
    vi.mocked(adminApi.listCorpAccess).mockResolvedValue([
      corp(),
      corp({
        corporation_id: 98000002,
        corporation_name: "Rich Corp",
        active: true,
        source: "admin",
        granted_at: "2026-07-01T00:00:00Z",
      }),
    ])
    renderAdmin()
    expect(await screen.findByText("Test Corp")).toBeInTheDocument()
    expect(screen.getByText("Off")).toBeInTheDocument()
    expect(screen.getByText("On")).toBeInTheDocument()
    expect(screen.getByText("Forever")).toBeInTheDocument() // active, no expiry
    expect(screen.getByText(/granted by admin/)).toBeInTheDocument()
  })

  it("gives access forever when no date is picked", async () => {
    const u = userEvent.setup()
    vi.mocked(adminApi.grantCorpAccess).mockResolvedValue(
      corp({ active: true, source: "admin", granted_at: "2026-07-09T00:00:00Z" }),
    )
    renderAdmin()
    await u.click(await screen.findByRole("button", { name: "Give access" }))
    await waitFor(() =>
      expect(adminApi.grantCorpAccess).toHaveBeenCalledWith(98000001, null),
    )
  })

  it("gives access until a picked date (end of day, UTC)", async () => {
    const u = userEvent.setup()
    vi.mocked(adminApi.grantCorpAccess).mockResolvedValue(
      corp({ active: true, granted_at: "2026-07-09T00:00:00Z" }),
    )
    renderAdmin()
    const date = await screen.findByLabelText(/Access until date for Test Corp/)
    await u.type(date, "2026-08-09")
    await u.click(screen.getByRole("button", { name: "Give access" }))
    await waitFor(() =>
      expect(adminApi.grantCorpAccess).toHaveBeenCalledWith(
        98000001,
        "2026-08-09T23:59:59Z",
      ),
    )
  })

  it("removes access behind a confirm", async () => {
    const u = userEvent.setup()
    vi.mocked(adminApi.listCorpAccess).mockResolvedValue([
      corp({ active: true, source: "admin", granted_at: "2026-07-01T00:00:00Z" }),
    ])
    vi.mocked(adminApi.revokeCorpAccess).mockResolvedValue(undefined)
    renderAdmin()
    await u.click(await screen.findByRole("button", { name: "Remove" }))
    // Destructive → confirm popup first (#36).
    expect(adminApi.revokeCorpAccess).not.toHaveBeenCalled()
    await u.click(screen.getByRole("button", { name: "Remove access" }))
    await waitFor(() =>
      expect(adminApi.revokeCorpAccess).toHaveBeenCalledWith(98000001),
    )
  })
})
