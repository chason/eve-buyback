import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as appraisalsApi from "../api/appraisals"
import * as locationsApi from "../api/locations"
import * as sdeApi from "../api/sde"
import Appraise from "./Appraise"

vi.mock("../api/appraisals")
vi.mock("../api/locations")
vi.mock("../api/pricing")
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
    vi.mocked(locationsApi.listLocations).mockResolvedValue([]) // none configured
    vi.mocked(sdeApi.searchTypes).mockResolvedValue([
      { type_id: 34, name: "Tritanium", market_group_id: 1 },
    ])
    vi.mocked(appraisalsApi.createAppraisal).mockResolvedValue({
      public_id: "xyz",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: "60003760",
      accepted_total: "0",
      rejected_count: 0,
      lines: [],
    })

    renderAppraise()

    await user.type(screen.getByLabelText("Search by name"), "trit")
    await user.click(await screen.findByText("Tritanium"))

    // The picked item shows up in the editable list.
    expect(screen.getByLabelText("Quantity for Tritanium")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Save appraisal" }))

    await waitFor(() =>
      expect(appraisalsApi.createAppraisal).toHaveBeenCalled(),
    )
    // useMutation passes a second context arg, so assert on the first argument.
    // No locations configured → no delivery id (backend defaults to the hub).
    expect(vi.mocked(appraisalsApi.createAppraisal).mock.calls[0][0]).toEqual({
      items: [{ type_id: 34, quantity: 1 }],
      paste: null,
      delivery_location_id: null,
    })
  })

  it("requires a drop-off when the corp has locations, and sends it", async () => {
    const user = userEvent.setup()
    vi.mocked(locationsApi.listLocations).mockResolvedValue([
      { location_id: "60003760", kind: "npc_station", name: "Jita 4-4" },
      { location_id: "1035000000001", kind: "structure", name: "1DQ - Palace" },
    ])
    vi.mocked(sdeApi.searchTypes).mockResolvedValue([
      { type_id: 34, name: "Tritanium", market_group_id: 1 },
    ])
    vi.mocked(appraisalsApi.createAppraisal).mockResolvedValue({
      public_id: "xyz",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: "60003760",
      accepted_total: "0",
      rejected_count: 0,
      lines: [],
    })

    renderAppraise()

    await user.type(screen.getByLabelText("Search by name"), "trit")
    await user.click(await screen.findByText("Tritanium"))

    // Submit is blocked until a drop-off is chosen.
    const button = screen.getByRole("button", { name: "Save appraisal" })
    expect(button).toBeDisabled()

    await user.selectOptions(
      await screen.findByLabelText("Drop-off location"),
      "1035000000001",
    )
    expect(button).toBeEnabled()
    await user.click(button)

    await waitFor(() =>
      expect(appraisalsApi.createAppraisal).toHaveBeenCalled(),
    )
    expect(
      vi.mocked(appraisalsApi.createAppraisal).mock.calls[0][0],
    ).toMatchObject({ delivery_location_id: "1035000000001" })
  })

  it("frames submission as a saved, corp-visible record (#31)", async () => {
    vi.mocked(locationsApi.listLocations).mockResolvedValue([])
    renderAppraise()
    expect(
      await screen.findByText(/saves an appraisal record/i),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Save appraisal" }),
    ).toBeInTheDocument()
  })
})
