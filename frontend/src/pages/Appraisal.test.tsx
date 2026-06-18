import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "../api/appraisals"
import * as authApi from "../api/auth"
import type { SessionUser } from "../api/types"
import Appraisal from "./Appraisal"

vi.mock("../api/appraisals")
vi.mock("../api/auth")

const sessionUser: SessionUser = {
  character_id: 1,
  character_name: "Pilot",
  corporation_id: 2,
  corporation_name: "Test Corp",
  role: "member",
  is_director: false,
  corporation_registered: true,
  can_open_contract: false,
}

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
    vi.mocked(authApi.getMe).mockResolvedValue(sessionUser)
    vi.mocked(api.getAppraisal).mockResolvedValue({
      public_id: "abc123",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: "60003760",
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
    // The total appears in the header, the contract panel, and the line total.
    expect(screen.getAllByText(/4,500\.00 ISK/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("Nonexistent")).toBeInTheDocument()
    expect(screen.getByText("Unknown item")).toBeInTheDocument()

    // The contract instructions hand over the entity + appraisal id.
    expect(screen.getByText(/Get paid/)).toBeInTheDocument()
    expect(screen.getByText("Test Corp")).toBeInTheDocument()
    expect(screen.getByText("abc123")).toBeInTheDocument()
  })

  it("keeps the full total accessible while the typewriter animates (#49)", async () => {
    // Motion allowed → the headline total types out, but its full value stays exposed
    // via aria-label the whole time.
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }))
    vi.mocked(authApi.getMe).mockResolvedValue(sessionUser)
    vi.mocked(api.getAppraisal).mockResolvedValue({
      public_id: "tw1",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: "60003760",
      accepted_total: "4500.00",
      rejected_count: 0,
      lines: [],
    })

    renderAt("tw1")

    expect(await screen.findByLabelText("4,500.00 ISK")).toBeInTheDocument()
    vi.unstubAllGlobals()
  })

  it("hides the contract panel when nothing was accepted", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(sessionUser)
    vi.mocked(api.getAppraisal).mockResolvedValue({
      public_id: "z9",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: "60003760",
      accepted_total: "0",
      rejected_count: 1,
      lines: [
        {
          type_id: null, type_name: "Nope", quantity: 1, status: "rejected",
          basis: null, percentage: null, unit_value: null, unit_price: null,
          line_total: "0.00", reason: "Unknown item",
        },
      ],
    })

    renderAt("z9")

    expect(await screen.findByText("Nope")).toBeInTheDocument()
    expect(screen.queryByText(/Get paid/)).not.toBeInTheDocument()
  })

  it("annotates lines priced at a rule-override hub", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(sessionUser)
    vi.mocked(api.getAppraisal).mockResolvedValue({
      public_id: "hub1",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: "60003760",
      accepted_total: "90.00",
      rejected_count: 0,
      lines: [
        {
          type_id: 34, type_name: "Tritanium", quantity: 10, status: "accepted",
          basis: "buy", percentage: "90", unit_value: "10.00", unit_price: "9.00",
          line_total: "90.00", reason: null,
          market_hub_id: "60008494",
          market_hub_name: "Amarr VIII (Oris) - Emperor Family Academy",
        },
        {
          type_id: 35, type_name: "Pyerite", quantity: 10, status: "accepted",
          basis: "buy", percentage: "90", unit_value: "2.00", unit_price: "1.80",
          line_total: "18.00", reason: null,
        },
      ],
    })

    renderAt("hub1")

    // The overridden line shows where it priced; the default-hub line doesn't.
    expect(
      await screen.findByText(/@ Amarr VIII \(Oris\)/),
    ).toBeInTheDocument()
    expect(screen.getAllByText(/@ /).length).toBe(1)
  })

  it("shows the reprocessed mineral breakdown for an ore line", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(sessionUser)
    vi.mocked(api.getAppraisal).mockResolvedValue({
      public_id: "ore1",
      created_by_character_id: 1,
      created_at: "2026-06-07T00:00:00Z",
      market_hub_id: "60003760",
      accepted_total: "36252.00",
      rejected_count: 0,
      lines: [
        {
          type_id: 1230, type_name: "Veldspar", quantity: 100, status: "accepted",
          basis: "buy", percentage: "100", unit_value: "362.52",
          unit_price: "362.52", line_total: "36252.00", reason: null,
          reprocess: {
            minerals: [
              { type_id: 34, type_name: "Tritanium", quantity: "362.52",
                unit_value: "100.00", value: "36252.00" },
            ],
            leftover_units: 0,
            leftover_value: "0",
          },
        },
      ],
    })

    renderAt("ore1")

    expect(await screen.findByText("Veldspar")).toBeInTheDocument()
    expect(screen.getByText(/Reprocessed into/)).toBeInTheDocument()
    // The mineral name + its market value appear in the breakdown row.
    expect(screen.getByText(/Tritanium/)).toBeInTheDocument()
  })

  const withContract = (publicId: string) => ({
    public_id: publicId,
    created_by_character_id: 1,
    created_at: "2026-06-07T00:00:00Z",
    market_hub_id: "60003760",
    accepted_total: "100.00",
    rejected_count: 0,
    contract_status: "in_progress",
    lines: [],
  })

  it("opens a matched contract in EVE when the session can (ADR-0038)", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue({
      ...sessionUser,
      can_open_contract: true,
    })
    vi.mocked(api.openContract).mockResolvedValue()
    vi.mocked(api.getAppraisal).mockResolvedValue(withContract("ctr1"))

    renderAt("ctr1")

    const btn = await screen.findByRole("button", { name: "Open in EVE" })
    await u.click(btn)

    expect(api.openContract).toHaveBeenCalledWith("ctr1")
    expect(
      await screen.findByText(/Opened in your EVE client/),
    ).toBeInTheDocument()
  })

  it("hides Open in EVE when the session lacks the scope", async () => {
    // sessionUser.can_open_contract is false (logged in before the scope shipped).
    vi.mocked(authApi.getMe).mockResolvedValue(sessionUser)
    vi.mocked(api.getAppraisal).mockResolvedValue(withContract("ctr2"))

    renderAt("ctr2")

    // The contract chip still renders…
    expect(await screen.findByText("In Progress")).toBeInTheDocument()
    // …but no Open-in-EVE affordance.
    expect(
      screen.queryByRole("button", { name: "Open in EVE" }),
    ).not.toBeInTheDocument()
  })
})
