import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as accountingApi from "../api/accounting"
import * as billingApi from "../api/billing"
import type { ProfitOut } from "../api/types"
import Profit from "./Profit"

vi.mock("../api/accounting")
vi.mock("../api/billing")

const PROFIT: ProfitOut = {
  since: "2026-08-01T00:00:00Z",
  until: null,
  revenue: "5200000000.00",
  sales_tax: "230000000.00",
  cost_of_goods: "4100000000.00",
  margin: "870000000.00",
  measured_margin: "820000000.00",
  estimated_margin: "50000000.00",
  fees: "120000000.00",
  write_downs: "0",
  other_expenses: "0",
  profit: "750000000.00",
  sale_count: 12,
  channels: [
    {
      channel: "market",
      revenue: "4000000000.00",
      sales_tax: "230000000.00",
      cost_of_goods: "3200000000.00",
      margin: "570000000.00",
      sale_count: 10,
    },
    {
      channel: "contract",
      revenue: "1200000000.00",
      sales_tax: "0",
      cost_of_goods: "900000000.00",
      margin: "300000000.00",
      sale_count: 2,
    },
    {
      channel: "direct",
      revenue: "0",
      sales_tax: "0",
      cost_of_goods: "0",
      margin: "0",
      sale_count: 0,
    },
  ],
}

function renderProfit() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Profit />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Profit", () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it("shows the period's profit in plain English, with the breakdown", async () => {
    vi.mocked(accountingApi.getProfit).mockResolvedValue({
      access: true,
      profit: PROFIT,
    })

    renderProfit()

    expect(screen.getByText("How we're doing")).toBeInTheDocument()
    // The headline: made 750M, from 12 sales.
    expect(await screen.findByText("We made")).toBeInTheDocument()
    expect(screen.getByText("750M ISK")).toBeInTheDocument()
    expect(screen.getByText("from 12 sales")).toBeInTheDocument()

    // Breakdown lines, plain English, spends with a minus sign.
    expect(screen.getByText("Sales brought in")).toBeInTheDocument()
    expect(screen.getByText("5.2B")).toBeInTheDocument()
    expect(
      screen.getByText("The stock we sold had cost us"),
    ).toBeInTheDocument()
    expect(screen.getByText("−4.1B")).toBeInTheDocument()
    expect(screen.getByText("−230M")).toBeInTheDocument() // sales tax
    expect(screen.getByText("−120M")).toBeInTheDocument() // broker fees
    // Zero lines stay hidden — no write-downs this period.
    expect(
      screen.queryByText("Stock marked down to match the market"),
    ).not.toBeInTheDocument()

    // The estimated slice is called out, never silently blended.
    expect(screen.getByText("Estimated value")).toBeInTheDocument()
    expect(
      screen.getByText(/50M ISK of this comes from stock whose cost was/),
    ).toBeInTheDocument()

    // Channels: market + contract listed; the empty direct channel isn't.
    expect(screen.getByText("Where it came from")).toBeInTheDocument()
    expect(screen.getByText("Market sales")).toBeInTheDocument()
    expect(screen.getByText("Contracts")).toBeInTheDocument()
    expect(
      screen.queryByText("Deals recorded by hand"),
    ).not.toBeInTheDocument()
  })

  it("says we're down when the period lost ISK", async () => {
    vi.mocked(accountingApi.getProfit).mockResolvedValue({
      access: true,
      profit: {
        ...PROFIT,
        profit: "-300000000.00",
        write_downs: "500000000.00",
      },
    })

    renderProfit()

    expect(await screen.findByText("We're down")).toBeInTheDocument()
    expect(screen.getByText("300M ISK")).toBeInTheDocument()
    expect(
      screen.getByText("Stock marked down to match the market"),
    ).toBeInTheDocument()
    expect(screen.getByText("−500M")).toBeInTheDocument()
  })

  it("defaults to this month and refetches when the period changes", async () => {
    vi.mocked(accountingApi.getProfit).mockResolvedValue({
      access: true,
      profit: PROFIT,
    })

    renderProfit()
    await screen.findByText("We made")

    // This month: since = the first of the current month, UTC (EVE time).
    const now = new Date()
    const monthStart = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1),
    ).toISOString()
    expect(accountingApi.getProfit).toHaveBeenCalledWith(monthStart, null)

    await userEvent.selectOptions(
      screen.getByLabelText(/Show/),
      "All time",
    )
    expect(accountingApi.getProfit).toHaveBeenLastCalledWith(null, null)
  })

  it("shows the empty state when nothing sold in the period", async () => {
    vi.mocked(accountingApi.getProfit).mockResolvedValue({
      access: true,
      profit: {
        ...PROFIT,
        revenue: "0",
        sale_count: 0,
        write_downs: "0",
        profit: "0",
      },
    })

    renderProfit()

    expect(
      await screen.findByText(/Nothing sold in this period yet/),
    ).toBeInTheDocument()
    expect(screen.queryByText("We made")).not.toBeInTheDocument()
  })

  it("shows the how-to-pay panel without access", async () => {
    vi.mocked(accountingApi.getProfit).mockResolvedValue({ access: false })
    vi.mocked(billingApi.getAccountingAccess).mockResolvedValue({
      active: false,
      expires_at: null,
      operator_character_name: null,
      payment_configured: false,
      period_days: 30,
      price_isk: 1000000000,
      reference: "buyback-ref",
    })

    renderProfit()

    expect(await screen.findByText("How we're doing")).toBeInTheDocument()
    expect(
      await screen.findByText(/doesn't have the accounting add-on yet/),
    ).toBeInTheDocument()
  })
})
