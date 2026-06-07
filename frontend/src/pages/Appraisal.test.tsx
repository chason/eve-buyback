import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "../api/appraisals"
import Appraisal from "./Appraisal"

vi.mock("../api/appraisals")

function renderAt(publicId: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/a/${publicId}`]}>
        <Routes>
          <Route path="/a/:publicId" element={<Appraisal />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Appraisal", () => {
  beforeEach(() => vi.resetAllMocks())

  it("renders priced and rejected lines with the accepted total", async () => {
    vi.mocked(api.getAppraisal).mockResolvedValue({
      public_id: "abc123",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: 60003760,
      accepted_total: "4500.0000000000",
      rejected_count: 1,
      lines: [
        {
          type_id: 34, type_name: "Tritanium", quantity: 1000, status: "accepted",
          basis: "buy", percentage: "90", unit_value: "5.00", unit_price: "4.50",
          line_total: "4500.00", reason: null,
        },
        {
          type_id: null, type_name: "Nonexistent", quantity: 5, status: "rejected",
          basis: null, percentage: null, unit_value: null, unit_price: null,
          line_total: "0.00", reason: "Unknown item",
        },
      ],
    })

    renderAt("abc123")

    expect(await screen.findByText("Tritanium")).toBeInTheDocument()
    // The total appears both in the header and as the single line's total.
    expect(screen.getAllByText(/4,500\.00 ISK/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("Nonexistent")).toBeInTheDocument()
    expect(screen.getByText("Unknown item")).toBeInTheDocument()
  })
})
