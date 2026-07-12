import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as accountingApi from "../api/accounting"
import * as billingApi from "../api/billing"
import type { InventoryOut } from "../api/types"
import Inventory from "./Inventory"

vi.mock("../api/accounting")
vi.mock("../api/billing")

const INVENTORY: InventoryOut = {
  total_cost: "4200000000.00",
  verified_cost: "3350000000.00",
  estimated_cost: "850000000.00",
  stale_days: 30,
  items: [
    {
      type_id: 34,
      type_name: "Tritanium",
      qty: 150,
      total_cost: "4200000000.00",
      oldest_days: 45,
      stale: true,
      any_estimated: true,
      lots: [
        {
          qty: 100,
          unit_cost: "4.00",
          total_cost: "400.00",
          acquired_at: "2026-05-28T12:00:00Z",
          days_held: 45,
          stale: true,
          cost_is_estimated: false,
        },
        {
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
  beforeEach(() => vi.resetAllMocks())

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

    // The item row: name, quantity, aging, and both plain-English chips.
    expect(screen.getByText("Tritanium")).toBeInTheDocument()
    expect(screen.getByText("150")).toBeInTheDocument()
    expect(screen.getByText("45 days")).toBeInTheDocument()
    expect(screen.getByText("Sitting a while")).toBeInTheDocument()
    // "Estimated value" appears as the summary card's label and the item's chip.
    expect(screen.getAllByText("Estimated value")).toHaveLength(2)
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
    expect(screen.getAllByText("Sitting a while")).toHaveLength(2) // item + old buy
    // Summary card + the item's chip + the estimated buy's chip.
    expect(screen.getAllByText("Estimated value")).toHaveLength(3)
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
        verified_cost: "0" },
    })

    renderInventory()

    expect(await screen.findByText(/Nothing in stock yet/)).toBeInTheDocument()
  })
})
