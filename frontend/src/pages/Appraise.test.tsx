import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as appraisalsApi from "../api/appraisals"
import * as sdeApi from "../api/sde"
import Appraise from "./Appraise"

vi.mock("../api/appraisals")
vi.mock("../api/sde")

function renderAppraise() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Appraise />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Appraise", () => {
  beforeEach(() => vi.resetAllMocks())

  it("adds a picked item and submits structured items", async () => {
    const user = userEvent.setup()
    vi.mocked(sdeApi.searchTypes).mockResolvedValue([
      { type_id: 34, name: "Tritanium", market_group_id: 1 },
    ])
    vi.mocked(appraisalsApi.createAppraisal).mockResolvedValue({
      public_id: "xyz",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: 60003760,
      accepted_total: "0",
      rejected_count: 0,
      lines: [],
    })

    renderAppraise()

    await user.type(screen.getByLabelText("Search by name"), "trit")
    await user.click(await screen.findByText("Tritanium"))

    // The picked item shows up in the editable list.
    expect(screen.getByLabelText("Quantity for Tritanium")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Appraise" }))

    await waitFor(() =>
      expect(appraisalsApi.createAppraisal).toHaveBeenCalled(),
    )
    // useMutation passes a second context arg, so assert on the first argument.
    expect(vi.mocked(appraisalsApi.createAppraisal).mock.calls[0][0]).toEqual({
      items: [{ type_id: 34, quantity: 1 }],
      paste: null,
    })
  })
})
