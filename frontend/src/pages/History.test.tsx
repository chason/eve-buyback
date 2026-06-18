import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as appraisalsApi from "../api/appraisals"
import * as authApi from "../api/auth"
import type { SessionUser } from "../api/types"
import History from "./History"

vi.mock("../api/auth")
vi.mock("../api/appraisals")

function user(role: SessionUser["role"]): SessionUser {
  return {
    character_id: 1,
    character_name: "Pilot",
    corporation_id: 2,
    corporation_name: "Corp",
    role,
    is_director: false,
    corporation_registered: true,
    can_open_contract: false,
  }
}
const member = user("member")

function renderHistory() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <History />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("History", () => {
  beforeEach(() => vi.resetAllMocks())

  it("lists appraisals with a link to each result", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(member)
    vi.mocked(appraisalsApi.listAppraisals).mockResolvedValue([
      {
        public_id: "abc123",
        created_by_character_id: 1,
        created_at: "2026-06-07T00:00:00Z",
        market_hub_id: "60003760",
        accepted_total: "4500.00",
        rejected_count: 2,
      },
    ])

    renderHistory()

    expect(await screen.findByText(/4,500\.00 ISK/)).toBeInTheDocument()
    const link = screen.getByRole("link", { name: "View" })
    expect(link).toHaveAttribute("href", "/a/abc123")

    // A non-zero rejected count renders as a danger status chip (#83).
    const chip = screen.getByText("2")
    expect(chip).toHaveClass("status", "status--danger")

    // The results table is framed in a HUD console panel (#81).
    expect(screen.getByRole("table").closest(".panel")).toBeInTheDocument()
  })

  it("shows the creator's name (not id) to a manager", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(appraisalsApi.listAppraisals).mockResolvedValue([
      {
        public_id: "abc123",
        created_by_character_id: 777,
        created_by_character_name: "Teammate Bob",
        created_at: "2026-06-07T00:00:00Z",
        market_hub_id: "60003760",
        accepted_total: "0",
        rejected_count: 0,
      },
    ])

    renderHistory()

    expect(await screen.findByText("Teammate Bob")).toBeInTheDocument()
    expect(screen.queryByText("777")).not.toBeInTheDocument()
  })

  it("shows an empty state when there are none", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(member)
    vi.mocked(appraisalsApi.listAppraisals).mockResolvedValue([])

    renderHistory()

    expect(await screen.findByText(/No appraisals yet/)).toBeInTheDocument()
  })

  it("offers Open in EVE per row only for matched contracts (ADR-0038)", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue({
      ...user("manager"),
      can_open_contract: true,
    })
    vi.mocked(appraisalsApi.openContract).mockResolvedValue()
    vi.mocked(appraisalsApi.listAppraisals).mockResolvedValue([
      {
        public_id: "matched1", created_by_character_id: 1,
        created_at: "2026-06-07T00:00:00Z", market_hub_id: "60003760",
        accepted_total: "100.00", rejected_count: 0, contract_status: "in_progress",
      },
      {
        public_id: "nocontract", created_by_character_id: 1,
        created_at: "2026-06-07T00:00:00Z", market_hub_id: "60003760",
        accepted_total: "50.00", rejected_count: 0,
      },
    ])

    renderHistory()

    // Exactly one row (the matched one) gets the action.
    const buttons = await screen.findAllByRole("button", { name: "Open in EVE" })
    expect(buttons).toHaveLength(1)

    await u.click(buttons[0])
    expect(appraisalsApi.openContract).toHaveBeenCalledWith("matched1")
  })
})
