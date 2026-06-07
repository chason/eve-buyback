import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as appraisalsApi from "../api/appraisals"
import * as authApi from "../api/auth"
import type { SessionUser } from "../api/types"
import History from "./History"

vi.mock("../api/auth")
vi.mock("../api/appraisals")

const member: SessionUser = {
  character_id: 1,
  character_name: "Pilot",
  corporation_id: 2,
  corporation_name: "Corp",
  role: "member",
  is_director: false,
  corporation_registered: true,
}

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
        market_hub_id: 60003760,
        accepted_total: "4500.00",
        rejected_count: 2,
      },
    ])

    renderHistory()

    expect(await screen.findByText(/4,500\.00 ISK/)).toBeInTheDocument()
    const link = screen.getByRole("link", { name: "View" })
    expect(link).toHaveAttribute("href", "/a/abc123")
  })

  it("shows an empty state when there are none", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(member)
    vi.mocked(appraisalsApi.listAppraisals).mockResolvedValue([])

    renderHistory()

    expect(await screen.findByText(/No appraisals yet/)).toBeInTheDocument()
  })
})
