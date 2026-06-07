import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as authApi from "../api/auth"
import * as pricingApi from "../api/pricing"
import * as sdeApi from "../api/sde"
import type { SessionUser } from "../api/types"
import Rules from "./Rules"

vi.mock("../api/auth")
vi.mock("../api/pricing")
vi.mock("../api/sde")

function user(role: SessionUser["role"]): SessionUser {
  return {
    character_id: 1,
    character_name: "Pilot",
    corporation_id: 2,
    corporation_name: "Corp",
    role,
    is_director: false,
    corporation_registered: true,
  }
}

function renderRules() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Rules />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Rules", () => {
  beforeEach(() => vi.resetAllMocks())

  it("shows each rule's backend-resolved target name", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([
      { target_kind: "market_group", target_id: 1, target_name: "Ore", basis: "buy", percentage: "80", enabled: true, reprocess: true },
      { target_kind: "type", target_id: 34, target_name: "Tritanium", basis: null, percentage: "90", enabled: true, reprocess: false },
    ])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([])

    renderRules()

    expect(await screen.findByText("Ore")).toBeInTheDocument()
    // A type target shows its name, not "Type 34".
    expect(screen.getByText("Tritanium")).toBeInTheDocument()
    expect(screen.queryByText("Type 34")).not.toBeInTheDocument()
  })

  it("lets a manager add a market-group rule", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([
      { market_group_id: 1, parent_id: null, name: "Ore" },
    ])
    vi.mocked(pricingApi.putRule).mockResolvedValue({
      target_kind: "market_group", target_id: 1, basis: "sell", percentage: "75", enabled: true, reprocess: true,
    })

    renderRules()

    await u.selectOptions(await screen.findByLabelText("Target kind"), "market_group")
    await u.selectOptions(screen.getByLabelText("Market group"), "1")
    await u.selectOptions(screen.getByLabelText("Basis"), "sell")
    const pct = screen.getByLabelText("Rule percentage")
    await u.clear(pct)
    await u.type(pct, "75")
    await u.click(screen.getByLabelText("Reprocess (ore → minerals)"))
    await u.click(screen.getByRole("button", { name: "Save rule" }))

    await waitFor(() => expect(pricingApi.putRule).toHaveBeenCalled())
    const call = vi.mocked(pricingApi.putRule).mock.calls[0]
    expect(call[0]).toBe("market_group")
    expect(call[1]).toBe(1)
    expect(call[2]).toEqual({
      basis: "sell",
      percentage: "75",
      enabled: true,
      reprocess: true,
    })
  })

  it("labels market-group options with their full tree path", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([
      { market_group_id: 10, parent_id: null, name: "Manufacture & Research" },
      { market_group_id: 11, parent_id: 10, name: "Materials" },
      { market_group_id: 12, parent_id: 11, name: "Standard Ores" },
    ])

    renderRules()

    const u = userEvent.setup()
    await u.selectOptions(
      await screen.findByLabelText("Target kind"),
      "market_group",
    )
    // The leaf "Standard Ores" is shown with its full disambiguating path.
    expect(
      screen.getByRole("option", {
        name: "Manufacture & Research / Materials / Standard Ores",
      }),
    ).toBeInTheDocument()
  })

  it("hides edit controls from a member", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("member"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([])

    renderRules()

    expect(
      await screen.findByText(/Only a Buyback Manager/),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Save rule" }),
    ).not.toBeInTheDocument()
  })
})
