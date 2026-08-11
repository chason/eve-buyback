import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as accountingApi from "../api/accounting"
import * as billingApi from "../api/billing"
import * as locationsApi from "../api/locations"
import * as sdeApi from "../api/sde"
import type { InventoryOut } from "../api/types"
import Inventory from "./Inventory"

vi.mock("../api/accounting")
vi.mock("../api/billing")
vi.mock("../api/locations")
vi.mock("../api/sde")

const INVENTORY: InventoryOut = {
  basis: "ledger",
  as_of: null,
  total_cost: "4200000000.00",
  verified_cost: "3350000000.00",
  estimated_cost: "850000000.00",
  stale_days: 30,
  worth_total: "4600000000.00",
  unrealized_total: "400000000.00",
  anything_priced: true,
  unpriced_types: 0,
  listed: [],
  items: [
    {
      type_id: 34,
      type_name: "Tritanium",
      qty: 150,
      qty_unbooked: 0,
      total_cost: "4200000000.00",
      oldest_days: 45,
      stale: true,
      any_estimated: true,
      worth: "4600000000.00",
      unrealized: "400000000.00",
      reprocessable: true,
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
          source: "buyback",
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
          source: "manual",
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
    vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue([])
    vi.mocked(accountingApi.listShipments).mockResolvedValue([])
    vi.mocked(accountingApi.getWalletDivision).mockResolvedValue({ division: null })
    vi.mocked(accountingApi.listReceivables).mockResolvedValue([])
    // No division names by default — pickers fall back to generic labels.
    vi.mocked(accountingApi.getDivisionNames).mockResolvedValue({
      wallet: [],
      hangar: [],
    })
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
        anything_priced: false, unpriced_types: 0 },
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

  it("labels wallet and hangar pickers with the corp's real division names (ADR-0048)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.getDivisionNames).mockResolvedValue({
      wallet: [{ division: 3, name: "Buyback ISK" }],
      hangar: [{ division: 2, name: "Deliveries" }],
    })
    vi.mocked(accountingApi.listHangars).mockResolvedValue([
      { location_id: "60003760", location_name: "Jita IV - Moon 4", division: 2 },
      { location_id: "60003760", location_name: "Jita IV - Moon 4", division: 5 },
    ])
    vi.mocked(locationsApi.listLocations).mockResolvedValue([
      { location_id: "60003760", kind: "npc_station", name: "Jita IV - Moon 4",
        system_name: "Jita" },
    ])

    renderInventory()

    // The configured-hangar list uses the real name, falling back per division.
    expect(
      await screen.findByText(/Jita IV - Moon 4 — Deliveries/),
    ).toBeInTheDocument()
    expect(screen.getByText(/Jita IV - Moon 4 — hangar 5/)).toBeInTheDocument()

    // The hangar picker: named division by name, the rest generic.
    const hangarSelect = screen.getByRole("combobox", { name: "Hangar division" })
    expect(
      within(hangarSelect).getByRole("option", { name: "Deliveries" }),
    ).toHaveValue("2")
    expect(
      within(hangarSelect).getByRole("option", { name: "Hangar 3" }),
    ).toBeInTheDocument()

    // The wallet picker: same treatment.
    const walletSelect = screen.getByRole("combobox", {
      name: "Buyback wallet division",
    })
    expect(
      within(walletSelect).getByRole("option", { name: "Buyback ISK" }),
    ).toHaveValue("3")
    expect(
      within(walletSelect).getByRole("option", { name: "Wallet division 1" }),
    ).toBeInTheDocument()
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

  it("hides Turned into minerals for a type without yield data", async () => {
    const u = userEvent.setup()
    const item = INVENTORY.items[0]
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        items: [
          { ...item, reprocessable: false },
          // A single-lot item without yields hides the row action too.
          {
            ...item,
            type_id: 44992,
            type_name: "PLEX",
            reprocessable: false,
            lots: [item.lots[0]],
          },
        ],
      },
    })

    renderInventory()

    // Expanding the buys shows the lot rows, but never the record action.
    await u.click(await screen.findByRole("button", { name: "2 buys" }))
    expect(
      screen.queryByRole("button", { name: "Turned into minerals" }),
    ).not.toBeInTheDocument()
  })

  it("shows a move suggestion as a plain-English card (#200)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue([
      {
        id: "sug-1", type_id: 34, type_name: "Tritanium",
        origin_location_id: "60008494", origin_name: "Amarr VIII",
        destination_location_id: "60003760", destination_name: "Jita IV - Moon 4",
        qty: 40, qty_listed: 0, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z",
      },
    ])

    renderInventory()

    // Plain English only — no "pairing", "reconciliation", or "lot"; the
    // actions are the confirm (#201) and the dismiss (#202).
    const card = await screen.findByText(
      /Looks like 40 Tritanium moved from Amarr VIII to Jita IV - Moon 4 — was this a move\?/,
    )
    expect(
      within(card.closest("li")!).getByRole("button", {
        name: "Yes, this was a move",
      }),
    ).toBeInTheDocument()
    expect(
      within(card.closest("li")!).getByRole("button", { name: "Not a move" }),
    ).toBeInTheDocument()
  })

  it("tells the listed-for-sale story on a sell-side card (#206)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue([
      {
        // Pure sell-side pair: everything sits in sell orders at Dodixie —
        // which needn't be a marked hangar at all.
        id: "sug-2", type_id: 34, type_name: "Tritanium",
        origin_location_id: "60008494", origin_name: "Amarr VIII",
        destination_location_id: "60011866", destination_name: "Dodixie IX",
        qty: 60, qty_listed: 60, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z",
      },
      {
        // Mixed pair: part counted in the hangar, part listed.
        id: "sug-3", type_id: 35, type_name: "Pyerite",
        origin_location_id: "60008494", origin_name: "Amarr VIII",
        destination_location_id: "60003760", destination_name: "Jita IV - Moon 4",
        qty: 100, qty_listed: 30, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z",
      },
    ])

    renderInventory()

    expect(
      await screen.findByText(
        /Looks like 60 Tritanium moved from Amarr VIII to Dodixie IX — they're listed for sale there\. Was this a move\?/,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /Looks like 100 Pyerite moved from Amarr VIII to Jita IV - Moon 4 — 30 of them are listed for sale there\. Was this a move\?/,
      ),
    ).toBeInTheDocument()
  })

  it("tells the already-sold story on a sold-evidence card (#207)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue([
      {
        // Pure sold pair: everything already sold at Jita at estimated cost.
        id: "sug-4", type_id: 34, type_name: "Tritanium",
        origin_location_id: "60008494", origin_name: "Amarr VIII",
        destination_location_id: "60003760", destination_name: "Jita IV - Moon 4",
        qty: 40, qty_listed: 0, qty_sold: 40, noticed_at: "2026-08-11T10:00:00Z",
      },
      {
        // The full ADR wording: part listed, part already sold.
        id: "sug-5", type_id: 35, type_name: "Pyerite",
        origin_location_id: "60008494", origin_name: "Amarr VIII",
        destination_location_id: "60011866", destination_name: "Dodixie IX",
        qty: 100, qty_listed: 60, qty_sold: 40, noticed_at: "2026-08-11T10:00:00Z",
      },
    ])

    renderInventory()

    expect(
      await screen.findByText(
        /Looks like 40 Tritanium moved from Amarr VIII to Jita IV - Moon 4 and already sold — was this a move\?/,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /Looks like 100 Pyerite moved from Amarr VIII to Dodixie IX — 60 of them are listed for sale there and 40 already sold\. Was this a move\?/,
      ),
    ).toBeInTheDocument()
  })

  it("confirms a move and the card leaves the pending list (#201)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    // Pending before the confirm; the invalidation refetch finds it handled.
    vi.mocked(accountingApi.listMoveSuggestions)
      .mockResolvedValueOnce([
        {
          id: "sug-1", type_id: 34, type_name: "Tritanium",
          origin_location_id: "60008494", origin_name: "Amarr VIII",
          destination_location_id: "60003760", destination_name: "Jita IV - Moon 4",
          qty: 40, qty_listed: 0, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z",
        },
      ])
      .mockResolvedValue([])
    vi.mocked(accountingApi.confirmMoveSuggestion).mockResolvedValue(undefined)

    renderInventory()

    // Two-step (#205): "Yes" opens the haul-cost prompt; confirming with the
    // field left empty books no cost.
    await u.click(
      await screen.findByRole("button", { name: "Yes, this was a move" }),
    )
    expect(
      screen.getByLabelText("What did the haul cost? (optional)"),
    ).toBeInTheDocument()
    await u.click(screen.getByRole("button", { name: "Confirm the move" }))
    expect(accountingApi.confirmMoveSuggestion).toHaveBeenCalledWith(
      "sug-1",
      undefined,
    )
    await waitFor(() =>
      expect(screen.queryByText(/was this a move\?/)).not.toBeInTheDocument(),
    )
  })

  it("passes the optional haul cost along with the confirm (#205)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions)
      .mockResolvedValueOnce([
        {
          id: "sug-1", type_id: 34, type_name: "Tritanium",
          origin_location_id: "60008494", origin_name: "Amarr VIII",
          destination_location_id: "60003760", destination_name: "Jita IV - Moon 4",
          qty: 40, qty_listed: 0, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z",
        },
      ])
      .mockResolvedValue([])
    vi.mocked(accountingApi.confirmMoveSuggestion).mockResolvedValue(undefined)

    renderInventory()

    await u.click(
      await screen.findByRole("button", { name: "Yes, this was a move" }),
    )
    await u.type(
      screen.getByLabelText("What did the haul cost? (optional)"),
      "1500000.25",
    )
    await u.click(screen.getByRole("button", { name: "Confirm the move" }))
    expect(accountingApi.confirmMoveSuggestion).toHaveBeenCalledWith(
      "sug-1",
      "1500000.25",
    )
  })

  it("cancelling the haul-cost prompt confirms nothing (#205)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue([
      {
        id: "sug-1", type_id: 34, type_name: "Tritanium",
        origin_location_id: "60008494", origin_name: "Amarr VIII",
        destination_location_id: "60003760", destination_name: "Jita IV - Moon 4",
        qty: 40, qty_listed: 0, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z",
      },
    ])

    renderInventory()

    await u.click(
      await screen.findByRole("button", { name: "Yes, this was a move" }),
    )
    await u.click(screen.getByRole("button", { name: "Cancel" }))
    expect(accountingApi.confirmMoveSuggestion).not.toHaveBeenCalled()
    expect(
      screen.queryByLabelText("What did the haul cost? (optional)"),
    ).not.toBeInTheDocument()
  })

  it("dismisses a move suggestion with Not a move (#202)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue([
      {
        id: "sug-1", type_id: 34, type_name: "Tritanium",
        origin_location_id: "60008494", origin_name: "Amarr VIII",
        destination_location_id: "60003760", destination_name: "Jita IV - Moon 4",
        qty: 40, qty_listed: 0, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z",
      },
    ])
    vi.mocked(accountingApi.dismissMoveSuggestion).mockImplementation(
      async () => {
        // The card leaves the list on the refetch the dismissal triggers.
        vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue([])
      },
    )

    renderInventory()

    const card = await screen.findByText(/was this a move\?/)
    await u.click(
      within(card.closest("li")!).getByRole("button", { name: "Not a move" }),
    )
    expect(accountingApi.dismissMoveSuggestion).toHaveBeenCalledWith("sug-1")
    await waitFor(() =>
      expect(screen.queryByText(/was this a move\?/)).not.toBeInTheDocument(),
    )
  })

  const CANDIDATES = [
    {
      id: "cand-jita", type_id: 34, type_name: "Tritanium",
      origin_location_id: "60003760", origin_name: "Jita IV - Moon 4",
      destination_location_id: "60008494", destination_name: "Amarr VIII",
      qty: 100, qty_listed: 0, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z", group_id: "lot-1",
    },
    {
      id: "cand-dodixie", type_id: 34, type_name: "Tritanium",
      origin_location_id: "60011866", origin_name: "Dodixie IX",
      destination_location_id: "60008494", destination_name: "Amarr VIII",
      qty: 50, qty_listed: 0, qty_sold: 0, noticed_at: "2026-08-11T10:00:00Z", group_id: "lot-1",
    },
  ]

  it("folds candidate origins into one card that asks which it was (#203)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue(CANDIDATES)

    renderInventory()

    // One card, one dropdown carrying every origin with what each is missing —
    // two controls total, no auto-choice, no jargon.
    const card = await screen.findByText(
      /Looks like some Tritanium moved to Amarr VIII — it could have come from more than one hangar\./,
    )
    const item = card.closest("li")!
    const pick = within(item).getByRole("combobox", {
      name: "Where did it come from?",
    })
    expect(
      within(pick).getByRole("option", { name: "From Jita IV - Moon 4 (100 missing)" }),
    ).toBeInTheDocument()
    expect(
      within(pick).getByRole("option", { name: "From Dodixie IX (50 missing)" }),
    ).toBeInTheDocument()
    expect(
      within(item).getByRole("button", { name: "Not a move" }),
    ).toBeInTheDocument()
    // No unqualified confirm button — picking an origin IS the confirm path.
    expect(
      within(item).queryByRole("button", { name: "Yes, this was a move" }),
    ).not.toBeInTheDocument()
  })

  it("confirms the picked candidate — and only that one (#203)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions)
      .mockResolvedValueOnce(CANDIDATES)
      .mockResolvedValue([])
    vi.mocked(accountingApi.confirmMoveSuggestion).mockResolvedValue(undefined)

    renderInventory()

    // Picking an origin from the dropdown opens the same haul-cost prompt as a
    // plain confirm (#205); confirming fires for the chosen candidate's id only.
    await u.selectOptions(
      await screen.findByRole("combobox", { name: "Where did it come from?" }),
      "cand-dodixie",
    )
    await u.click(screen.getByRole("button", { name: "Confirm the move" }))
    expect(accountingApi.confirmMoveSuggestion).toHaveBeenCalledTimes(1)
    expect(accountingApi.confirmMoveSuggestion).toHaveBeenCalledWith(
      "cand-dodixie",
      undefined,
    )
  })

  it("Not a move on a candidate card dismisses every candidate (#203)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listMoveSuggestions).mockResolvedValue(CANDIDATES)
    vi.mocked(accountingApi.dismissMoveSuggestion).mockResolvedValue(undefined)

    renderInventory()

    const card = await screen.findByText(/could have come from more than one/)
    await u.click(
      within(card.closest("li")!).getByRole("button", { name: "Not a move" }),
    )
    await waitFor(() =>
      expect(accountingApi.dismissMoveSuggestion).toHaveBeenCalledTimes(2),
    )
    expect(accountingApi.dismissMoveSuggestion).toHaveBeenCalledWith("cand-jita")
    expect(accountingApi.dismissMoveSuggestion).toHaveBeenCalledWith(
      "cand-dodixie",
    )
  })

  it("tells the dismissed and withdrawn stories in the log (#202)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listReconciliationEvents).mockResolvedValue([
      {
        kind: "move_dismissed", type_id: 34, type_name: "Tritanium",
        location_id: "60008494", location_name: "Amarr VIII",
        qty: 40, unit_cost: null, booked: false, flagged: false,
        note: "Not a move — dismissed by Boss",
        occurred_at: "2026-08-11T11:00:00Z",
      },
      {
        kind: "move_withdrawn", type_id: 35, type_name: "Pyerite",
        location_id: "60003760", location_name: "Jita IV - Moon 4",
        qty: 12, unit_cost: null, booked: false, flagged: false,
        note: "The found stock is no longer there",
        occurred_at: "2026-08-11T10:00:00Z",
      },
    ])

    renderInventory()

    expect(
      await screen.findByText(
        /Decided 40 Tritanium didn't move from Amarr VIII — everything stays as recorded\./,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /The move hint for 12 Pyerite from Jita IV - Moon 4 no longer adds up — it was taken back\./,
      ),
    ).toBeInTheDocument()
  })

  it("records a haul between two marked hangars (#208)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listHangars).mockResolvedValue([
      { location_id: "60003760", location_name: "Jita IV - Moon 4", division: 2 },
      { location_id: "60008494", location_name: "Amarr VIII", division: 2 },
    ])
    vi.mocked(sdeApi.searchTypes).mockResolvedValue([
      { type_id: 34, name: "Tritanium", market_group_id: 1857 },
    ])
    vi.mocked(accountingApi.recordShipment).mockResolvedValue(undefined)

    renderInventory()

    const heading = await screen.findByRole("heading", {
      name: "Hauls between hangars",
    })
    const section = within(heading.closest("section")!)
    await u.type(
      await section.findByPlaceholderText("Search items to haul…"), "trit",
    )
    await u.click(await section.findByRole("button", { name: "Tritanium" }))
    await u.type(section.getByLabelText("How many"), "400")
    await u.selectOptions(
      section.getByRole("combobox", { name: "From hangar" }), "60003760",
    )
    await u.selectOptions(
      section.getByRole("combobox", { name: "To hangar" }), "60008494",
    )
    await u.click(section.getByRole("button", { name: "Record the haul" }))

    expect(accountingApi.recordShipment).toHaveBeenCalledWith({
      type_id: 34, qty: 400,
      origin_location_id: "60003760", destination_location_id: "60008494",
    })
    expect(
      await section.findByText("On the road — mark it arrived when it lands."),
    ).toBeInTheDocument()
  })

  it("marks an open haul arrived and it leaves the list (#208)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listShipments).mockResolvedValue([
      {
        id: "ship-1", type_id: 34, type_name: "Tritanium",
        origin_location_id: "60003760", origin_name: "Jita IV - Moon 4",
        destination_location_id: "60008494", destination_name: "Amarr VIII",
        qty: 400, sent_at: "2026-08-11T10:00:00Z",
      },
    ])
    vi.mocked(accountingApi.markShipmentArrived).mockImplementation(
      async () => {
        // The haul leaves the list on the refetch the arrival triggers.
        vi.mocked(accountingApi.listShipments).mockResolvedValue([])
      },
    )

    renderInventory()

    // Plain English: what's on the road, from where to where — and, with no
    // second hangar marked, the record form yields to a hint instead.
    const haul = await screen.findByText(
      /400 Tritanium on its way from Jita IV - Moon 4 to Amarr VIII/,
    )
    expect(
      screen.getByText(/mark a second one above first/),
    ).toBeInTheDocument()
    await u.click(
      within(haul.closest("li")!).getByRole("button", { name: "It arrived" }),
    )
    expect(accountingApi.markShipmentArrived).toHaveBeenCalledWith("ship-1")
    await waitFor(() =>
      expect(screen.queryByText(/on its way from/)).not.toBeInTheDocument(),
    )
  })

  it("tells the haul stories in the log (#208)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listReconciliationEvents).mockResolvedValue([
      {
        kind: "shipment_recorded", type_id: 34, type_name: "Tritanium",
        location_id: "60003760", location_name: "Jita IV - Moon 4",
        qty: 400, unit_cost: null, booked: false, flagged: false,
        note: "on its way to Amarr VIII, sent by Boss",
        occurred_at: "2026-08-11T10:00:00Z",
      },
      {
        kind: "shipment_arrived", type_id: 34, type_name: "Tritanium",
        location_id: "60008494", location_name: "Amarr VIII",
        qty: 400, unit_cost: null, booked: false, flagged: false,
        note: "hauled from Jita IV - Moon 4, marked arrived by Boss",
        occurred_at: "2026-08-11T14:00:00Z",
      },
    ])

    renderInventory()

    expect(
      await screen.findByText(
        /Sent 400 Tritanium from Jita IV - Moon 4 — on its way to Amarr VIII, sent by Boss\./,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /400 Tritanium arrived at Amarr VIII — hauled from Jita IV - Moon 4, marked arrived by Boss\./,
      ),
    ).toBeInTheDocument()
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

  it("picks the buyback wallet division to switch sales recording on (#156)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.setWalletDivision).mockResolvedValue({ division: 3 })

    renderInventory()

    const select = await screen.findByRole("combobox", {
      name: "Buyback wallet division",
    })
    await u.selectOptions(select, "3")
    expect(accountingApi.setWalletDivision).toHaveBeenCalledWith(3)
  })

  it("records an off-game sale by hand with a required note (#158)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(locationsApi.listLocations).mockResolvedValue([
      { location_id: "60003760", kind: "npc_station", name: "Jita IV - Moon 4",
        system_name: "Jita" },
    ])
    vi.mocked(sdeApi.searchTypes).mockResolvedValue([
      { type_id: 34, name: "Tritanium", market_group_id: 1857 },
    ])
    vi.mocked(accountingApi.recordManualSale).mockResolvedValue({
      stock_was_missing: false,
    })

    renderInventory()

    // Sale is the default kind; pick the item, fill the rest.
    await u.type(
      await screen.findByPlaceholderText("Search items…"), "trit",
    )
    await u.click(await screen.findByRole("button", { name: "Tritanium" }))
    await u.type(screen.getByLabelText("How many"), "60")
    await u.selectOptions(
      screen.getByRole("combobox", { name: "Where" }), "60003760",
    )
    await u.type(screen.getByLabelText("ISK per unit"), "5.00")
    const record = screen.getByRole("button", { name: "Add to the books" })
    expect(record).toBeDisabled() // no note yet — the note is required
    await u.type(screen.getByPlaceholderText(/What happened/), "sold on comms")
    await u.click(record)

    // The number input normalizes "5.00" to "5" — an equivalent Decimal string.
    expect(accountingApi.recordManualSale).toHaveBeenCalledWith({
      type_id: 34, qty: 60, unit_proceeds: "5",
      location_id: "60003760", note: "sold on comms",
    })
    expect(await screen.findByText("Recorded.")).toBeInTheDocument()
  })

  it("prefills the sale form from a shortfall's Record the sale (#158)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listReconciliationEvents).mockResolvedValue([
      {
        kind: "shortfall", type_id: 34, type_name: "Tritanium",
        location_id: "60003760", location_name: "Jita IV - Moon 4",
        qty: 600, unit_cost: null, booked: false, flagged: true,
        note: null, occurred_at: "2026-08-11T10:00:00Z",
      },
    ])

    renderInventory()

    await u.click(
      await screen.findByRole("button", { name: "Record the sale" }),
    )
    // The form now targets the missing stock: item picked, quantity prefilled.
    expect(screen.getByRole("button", { name: "Change item" })).toBeInTheDocument()
    expect(screen.getByLabelText("How many")).toHaveValue(600)
  })

  it("lists open owed ISK and marks it paid (#158)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listReceivables).mockResolvedValue([
      { id: "r1", amount: "50000000.00",
        note: "Buyer paid into division 1 by mistake",
        incurred_at: "2026-08-11T09:00:00Z", cleared_at: null,
        cleared_note: null },
    ])
    vi.mocked(accountingApi.clearReceivable).mockResolvedValue({
      id: "r1", amount: "50000000.00", note: "…",
      incurred_at: "2026-08-11T09:00:00Z",
      cleared_at: "2026-08-11T12:00:00Z", cleared_note: null,
    })

    renderInventory()

    expect(
      await screen.findByRole("heading", { name: "ISK owed to us" }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Buyer paid into division 1 by mistake/),
    ).toBeInTheDocument()
    await u.click(screen.getByRole("button", { name: "Mark paid" }))
    expect(accountingApi.clearReceivable).toHaveBeenCalledWith("r1")
  })

  it("badges hand-entered buys (#158)", async () => {
    const u = userEvent.setup()
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })

    renderInventory()

    await u.click(await screen.findByRole("button", { name: "2 buys" }))
    expect(screen.getByText("Entered by hand")).toBeInTheDocument()
  })

  it("dashes unpriced items and counts them under the table", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        worth_total: "0",
        unrealized_total: "0",
        anything_priced: false,
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

  it("tells the automatic-reprocess story in the log (ADR-0050)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY,
    })
    vi.mocked(accountingApi.listReconciliationEvents).mockResolvedValue([
      {
        kind: "reprocess_recorded", type_id: 1230, type_name: "Veldspar",
        location_id: "60003760", location_name: "Jita IV - Moon 4",
        qty: 600, unit_cost: null, booked: false, flagged: false,
        note: null, occurred_at: "2026-07-14T10:00:00Z",
      },
    ])

    renderInventory()

    expect(
      await screen.findByText(
        /600 Veldspar at Jita IV - Moon 4 was turned into minerals — recorded automatically/,
      ),
    ).toBeInTheDocument()
    // Recorded, not a to-do: no "Record it" button rides this entry.
    expect(screen.queryByText("Record it")).not.toBeInTheDocument()
  })

  it("shows the hangar snapshot with its taken-at line (ADR-0050)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        basis: "hangar",
        as_of: new Date(Date.now() - 30 * 60_000).toISOString(),
      },
    })

    renderInventory()

    expect(
      await screen.findByText(/What's sitting in the buyback hangars/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Last looked in the hangars 30 minutes ago/),
    ).toBeInTheDocument()
    // The books-view hint belongs to the ledger fallback only.
    expect(screen.queryByText(/This is the books view/)).not.toBeInTheDocument()
  })

  it("explains the books view until a hangar snapshot exists (ADR-0050)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: INVENTORY, // basis "ledger"
    })

    renderInventory()

    expect(await screen.findByText(/This is the books view/)).toBeInTheDocument()
  })

  it("chips hangar stock the books don't explain yet (ADR-0050)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        basis: "hangar",
        as_of: "2026-07-14T12:00:00Z",
        items: [
          {
            ...INVENTORY.items[0],
            type_name: "Pyerite",
            qty: 250,
            qty_unbooked: 250,
            total_cost: "0",
            oldest_days: null,
            stale: false,
            any_estimated: false,
            lots: [],
          },
        ],
      },
    })

    renderInventory()

    expect(await screen.findByText("Pyerite")).toBeInTheDocument()
    expect(screen.getByText("Not on the books yet")).toBeInTheDocument()
    // Unknown age reads as a dash with a plain-English tooltip.
    const dash = screen
      .getAllByText("—")
      .find((el) => el.title.includes("when this arrived"))
    expect(dash).toBeTruthy()
  })

  it("chips the unbooked part of a partially booked row (ADR-0050)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        basis: "hangar",
        as_of: "2026-07-14T12:00:00Z",
        items: [
          {
            ...INVENTORY.items[0],
            qty: 250,
            qty_unbooked: 100,
          },
        ],
      },
    })

    renderInventory()

    expect(
      await screen.findByText("100 not on the books yet"),
    ).toBeInTheDocument()
  })

  it("keeps the valuation cards when the hangar is empty but the books are priced (ADR-0050)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        basis: "hangar",
        as_of: "2026-07-14T12:00:00Z",
        items: [],
        unpriced_types: 0,
      },
    })

    renderInventory()

    expect(
      await screen.findByText("Nothing in the buyback hangars right now."),
    ).toBeInTheDocument()
    // The cards follow the ledger-wide totals, not the (empty) table.
    expect(screen.getByText("If we sold it all today")).toBeInTheDocument()
    expect(screen.getByText("4.6B ISK")).toBeInTheDocument()
  })

  it("lists sell-order stock in its own section (ADR-0050)", async () => {
    vi.mocked(accountingApi.getInventory).mockResolvedValue({
      access: true,
      inventory: {
        ...INVENTORY,
        basis: "hangar",
        as_of: "2026-07-14T12:00:00Z",
        listed: [
          {
            type_id: 34,
            type_name: "Tritanium",
            location_id: "60008494",
            location_name: "Amarr VIII (Oris)",
            qty: 200,
            worth: "760.00",
          },
        ],
      },
    })

    renderInventory()

    expect(await screen.findByText("Listed for sale")).toBeInTheDocument()
    const section = screen.getByText("Listed for sale").closest("section")!
    expect(within(section).getByText("200")).toBeInTheDocument()
    expect(within(section).getByText("Amarr VIII (Oris)")).toBeInTheDocument()
  })
})
