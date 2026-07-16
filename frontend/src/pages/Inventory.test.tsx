import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as accountingApi from "../api/accounting"
import * as billingApi from "../api/billing"
import * as locationsApi from "../api/locations"
import type { InventoryOut } from "../api/types"
import Inventory from "./Inventory"

vi.mock("../api/accounting")
vi.mock("../api/billing")
vi.mock("../api/locations")

const INVENTORY: InventoryOut = {
  total_cost: "4200000000.00",
  verified_cost: "3350000000.00",
  estimated_cost: "850000000.00",
  stale_days: 30,
  worth_total: "4600000000.00",
  unrealized_total: "400000000.00",
  unpriced_types: 0,
  items: [
    {
      type_id: 34,
      type_name: "Tritanium",
      qty: 150,
      total_cost: "4200000000.00",
      oldest_days: 45,
      stale: true,
      any_estimated: true,
      worth: "4600000000.00",
      unrealized: "400000000.00",
      lots: [
        {
          id: "lot-old",
          qty: 100,
          unit_cost: "4.00",
          total_cost: "400.00",
          acquired_at: "2026-05-28T12:00:00Z",
          days_held: 45,
          stale: true,
          cost_is_estimated: false,
        },
        {
          id: "lot-new",
          qty: 50,
          unit_cost: "5.00",
          total_cost: "250.00",
          acquired_at: "2026-07-10T12:00:00Z",
          days_held: 2,
          stale: false,
          cost_is_estimated: true,
        },
      ],
    },
  ],
}

function renderInventory() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Inventory />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Inventory", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    // The hangar + check sections always query these; individual tests override.
    vi.mocked(accountingApi.listHangars).mockResolvedValue([])
    vi.mocked(locationsApi.listLocations).mockResolvedValue([])
    vi.mocked(accountingApi.listReconciliationEvents).mockResolvedValue([])
  })

  it("shows holdings at what we paid, compact, with plain-English flags", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })

    renderInventory()

    expect(await screen.findByText("What we've got")).toBeInTheDocument()
    // Headline cards: compact ISK (4.2B), never raw decimals.
    expect(screen.getByText("4.2B ISK")).toBeInTheDocument()
    expect(screen.getByText("3.3B ISK")).toBeInTheDocument()
    expect(screen.getByText("850M ISK")).toBeInTheDocument()

    // The item row: name, quantity, and aging.
    expect(screen.getByText("Tritanium")).toBeInTheDocument()
    expect(screen.getByText("150")).toBeInTheDocument()
    // Stale aging is the day count itself turned danger-red, explained on hover.
    const days = screen.getByText("45 days")
    expect(days).toHaveClass("stale-days")
    expect(days).toHaveAttribute("title", "Sitting a while")
    // "Estimated value" appears as the summary card's label and the item's chip —
    // the chip rides the price cell, next to what we paid.
    const chips = screen
      .getAllByText("Estimated value")
      .filter((el) => el.classList.contains("status"))
    expect(chips).toHaveLength(1)
    expect(chips[0].closest("td")).toHaveTextContent("4.2B")
  })

  it("expands an item into its individual buys", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })

    renderInventory()

    await u.click(await screen.findByRole("button", { name: "2 buys" }))
    // Per-buy detail: exact unit prices and each buy's own aging + flags.
    expect(screen.getByText("4.00 ISK each")).toBeInTheDocument()
    expect(screen.getByText("5.00 ISK each")).toBeInTheDocument()
    // The item's oldest-days readout + the old buy's own: both red with the tooltip.
    expect(screen.getAllByTitle("Sitting a while")).toHaveLength(2)
    // Estimated chips: the item's (price cell) + the estimated buy's (its price cell).
    const chips = screen
      .getAllByText("Estimated value")
      .filter((el) => el.classList.contains("status"))
    expect(chips).toHaveLength(2)
    expect(chips[1].closest("td")).toHaveTextContent("5.00 ISK each")
  })

  it("shows the how-to-pay panel instead of data without access (402)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({ access: false })
    vi.mocked(billingApi.getAccountingAccess).mockResolvedValue({
      active: false,
      expires_at: null,
      payment_configured: true,
      price_isk: 250000000,
      period_days: 30,
      reference: "BB-98000001",
      operator_character_name: "Cash Collector",
    })

    renderInventory()

    expect(
      await screen.findByText(/doesn't have the accounting add-on yet/),
    ).toBeInTheDocument()
    expect(screen.getByText("Paid features")).toBeInTheDocument()
    expect(screen.queryByRole("table")).not.toBeInTheDocument()
  })

  it("shows a friendly empty state when nothing is in stock", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: { ...INVENTORY, items: [], estimated_cost: "0", total_cost: "0",
        verified_cost: "0", worth_total: "0", unrealized_total: "0",
        unpriced_types: 0 },
    })

    renderInventory()

    expect(await screen.findByText(/Nothing in stock yet/)).toBeInTheDocument()
  })

  it("shows worth-now, a signed gain card, and green figures when up (#153)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })

    renderInventory()

    expect(await screen.findByText("If we sold it all today")).toBeInTheDocument()
    expect(screen.getByText("4.6B ISK")).toBeInTheDocument()
    // The gain card is signed and NOT marked as a loss.
    const gain = screen.getByText("+400M ISK")
    expect(gain).not.toHaveClass("worth-loss")
    // The item's Worth now cell is plain green (no loss treatment).
    expect(screen.getByText("4.6B").closest(".worth-loss")).toBeNull()
  })

  it("marks a losing position red with a plain-English tooltip", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        worth_total: "3900000000.00",
        unrealized_total: "-300000000.00",
        items: [{
          ...INVENTORY.items[0],
          worth: "3900000000.00",
          unrealized: "-300000000.00",
        }],
      },
    })

    renderInventory()

    // The loss card keeps its minus sign and reads danger-red.
    expect(await screen.findByText("-300M ISK")).toHaveClass("worth-loss")
    // The item's Worth now figure carries the tooltip'd loss treatment.
    const cell = screen.getByTitle("Worth less than we paid")
    expect(cell).toHaveClass("worth-loss")
    expect(cell).toHaveTextContent("3.9B")
  })

  it("lists marked hangars and adds one from the drop-off picker (#154)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listHangars).mockResolvedValue([
      { location_id: "60003760", location_name: "Jita IV - Moon 4", division: 2 },
    ])
    vi.mocked(locationsApi.listLocations).mockResolvedValue([
      { location_id: "60003760", kind: "npc_station", name: "Jita IV - Moon 4",
        system_name: "Jita" },
    ])
    vi.mocked(accountingApi.addHangar).mockResolvedValue({
      location_id: "60003760", location_name: "Jita IV - Moon 4", division: 3,
    })

    renderInventory()

    expect(await screen.findByText(/Jita IV - Moon 4 — hangar 2/)).toBeInTheDocument()

    await u.selectOptions(
      screen.getByRole("combobox", { name: "Hangar location" }), "60003760",
    )
    await u.selectOptions(
      screen.getByRole("combobox", { name: "Hangar division" }), "3",
    )
    await u.click(screen.getByRole("button", { name: "Add hangar" }))
    expect(accountingApi.addHangar).toHaveBeenCalledWith("60003760", 3)
  })

  it("points at the Locations page when no drop-offs exist yet", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })

    renderInventory()

    expect(
      await screen.findByText(/Add a drop-off location/),
    ).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Locations" })).toHaveAttribute(
      "href",
      "/locations",
    )
  })

  it("tells the hangar-check story in plain English, flagged in red (#155)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listReconciliationEvents).mockResolvedValue([
      {
        kind: "excess", type_id: 34, type_name: "Tritanium",
        location_id: "60003760", location_name: "Jita IV - Moon 4",
        qty: 40000, unit_cost: "3.60", booked: true, flagged: false,
        note: null, occurred_at: "2026-07-14T10:00:00Z",
      },
      {
        kind: "shortfall", type_id: 35, type_name: "Pyerite",
        location_id: "60003760", location_name: "Jita IV - Moon 4",
        qty: 12, unit_cost: null, booked: false, flagged: true,
        note: null, occurred_at: "2026-07-14T09:00:00Z",
      },
    ])

    renderInventory()

    expect(
      await screen.findByText(
        /Found 40,000 Tritanium at Jita IV - Moon 4 — added at estimated value \(3\.60 ISK each\)\./,
      ),
    ).toBeInTheDocument()
    const missing = screen.getByText(
      /12 Pyerite missing at Jita IV - Moon 4 — sold or moved outside the app\?/,
    )
    expect(missing.closest("li")).toHaveClass("recon-flagged")
  })

  it("runs a hangar check on demand and summarizes the result", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.runHangarCheck).mockResolvedValue({
      lots_added: 2,
      flagged: 1,
    })

    renderInventory()

    await u.click(
      await screen.findByRole("button", { name: "Check the hangar now" }),
    )
    expect(accountingApi.runHangarCheck).toHaveBeenCalled()
    expect(
      await screen.findByText(/Done — 2 items added, 1 flagged for a look\./),
    ).toBeInTheDocument()
  })

  it("records a reprocess from a buy row with pre-filled outputs (#177)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.getReprocessPreview).mockResolvedValue({
      lot_id: "lot-old",
      type_id: 34,
      type_name: "Veldspar",
      qty_remaining: 100,
      outputs: [
        { type_id: 34, type_name: "Tritanium", quantity: 362 },
        { type_id: 35, type_name: "Pyerite", quantity: 9 },
      ],
    })
    vi.mocked(accountingApi.recordReprocess).mockResolvedValue({ children: [] })

    renderInventory()

    // Open the buys, then the record form from the oldest one.
    await u.click(await screen.findByRole("button", { name: "2 buys" }))
    await u.click(
      screen.getAllByRole("button", { name: "Turned into minerals" })[0],
    )
    expect(accountingApi.getReprocessPreview).toHaveBeenCalledWith("lot-old")
    expect(
      await screen.findByText(/what we paid for it carries over/i),
    ).toBeInTheDocument()
    // Pre-filled from base yields, editable.
    const trit = screen.getByLabelText("Tritanium")
    expect(trit).toHaveValue(362)
    await u.clear(trit)
    await u.type(trit, "360")

    await u.click(screen.getByRole("button", { name: "Record it" }))
    expect(accountingApi.recordReprocess).toHaveBeenCalledWith("lot-old", 100, [
      { type_id: 34, quantity: 360 },
      { type_id: 35, quantity: 9 },
    ])
  })

  it("offers Record it on a reprocess suggestion (#177)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listReconciliationEvents).mockResolvedValue([
      {
        kind: "reprocess_hint", type_id: 34, type_name: "Tritanium",
        location_id: "60003760", location_name: "Jita IV - Moon 4",
        qty: 600, unit_cost: null, booked: false, flagged: true,
        note: null, occurred_at: "2026-07-14T10:00:00Z",
      },
    ])
    vi.mocked(accountingApi.getReprocessPreview).mockResolvedValue({
      lot_id: "lot-old", type_id: 34, type_name: "Tritanium",
      qty_remaining: 100, outputs: [],
    })

    renderInventory()

    expect(
      await screen.findByText(/was turned into minerals — record it/i),
    ).toBeInTheDocument()
    // "Record it" opens the form against the type's oldest buy (FIFO).
    await u.click(screen.getByRole("button", { name: "Record it" }))
    expect(accountingApi.getReprocessPreview).toHaveBeenCalledWith("lot-old")
  })

  it("dashes unpriced items and counts them under the table", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        worth_total: "0",
        unrealized_total: "0",
        unpriced_types: 1,
        items: [{ ...INVENTORY.items[0], worth: null, unrealized: null }],
      },
    })

    renderInventory()

    expect(await screen.findByText("—")).toBeInTheDocument()
    expect(
      screen.getByText(/1 item has no current market price/),
    ).toBeInTheDocument()
    // Nothing is priced → the valuation cards stay hidden.
    expect(screen.queryByText("If we sold it all today")).not.toBeInTheDocument()
  })
})
