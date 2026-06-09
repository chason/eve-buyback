import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as authApi from "../api/auth"
import * as pricingApi from "../api/pricing"
import * as sdeApi from "../api/sde"
import type { SessionUser } from "../api/types"
import Config from "./Config"

vi.mock("../api/auth")
vi.mock("../api/pricing")
vi.mock("../api/sde")

const CUSTOM_HUB = "custom" // the hub picker's "Other NPC station…" option value

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

const config = {
  corporation_id: 2,
  market_hub_id: "60003760",
  market_hub_kind: "npc_station" as const,
  market_region_id: null,
  market_hub_name: "Jita IV - Moon 4 - Caldari Navy Assembly Plant",
  default_basis: "buy" as const,
  default_percentage: "90",
  aggregate_field: "percentile" as const,
  default_accepted: true,
}

function renderConfig() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Config />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Config", () => {
  beforeEach(() => vi.resetAllMocks())

  it("lets a manager edit and save the config", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)
    vi.mocked(pricingApi.updateConfig).mockResolvedValue(config)

    renderConfig()

    const pct = await screen.findByLabelText("Default percentage")
    await u.clear(pct)
    await u.type(pct, "85")
    // Flip to whitelist mode (buy nothing unless a rule accepts).
    await u.click(screen.getByLabelText(/Accept items by default/))
    await u.click(screen.getByRole("button", { name: "Save config" }))

    await waitFor(() => expect(pricingApi.updateConfig).toHaveBeenCalled())
    expect(vi.mocked(pricingApi.updateConfig).mock.calls[0][0]).toEqual({
      market_hub_id: "60003760",
      market_hub_kind: "npc_station",
      default_basis: "buy",
      default_percentage: "85",
      aggregate_field: "percentile",
      default_accepted: false,
    })
  })

  it("prices at a custom NPC station picked from the search list", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)
    vi.mocked(pricingApi.updateConfig).mockResolvedValue(config)
    vi.mocked(sdeApi.searchStations).mockResolvedValue([
      {
        station_id: 60012345,
        name: "Korsiki VII - Moon 1 - Expert Distribution Warehouse",
        system_name: "Korsiki",
        region_id: 10000033,
      },
    ])

    renderConfig()

    const hub = await screen.findByLabelText("Market hub")
    await u.selectOptions(hub, CUSTOM_HUB)
    await u.type(screen.getByLabelText("Search station"), "korsiki")
    // Pick the match from the dropdown ("System - Station").
    await u.click(await screen.findByText(/Korsiki - Korsiki VII/))
    await u.click(screen.getByRole("button", { name: "Save config" }))

    await waitFor(() => expect(pricingApi.updateConfig).toHaveBeenCalled())
    expect(vi.mocked(pricingApi.updateConfig).mock.calls[0][0]).toMatchObject({
      market_hub_id: "60012345",
      market_hub_kind: "npc_station",
    })
  })

  it("shows a member a read-only view", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("member"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)

    renderConfig()

    expect(await screen.findByLabelText("Default percentage")).toBeDisabled()
    expect(
      screen.queryByRole("button", { name: "Save config" }),
    ).not.toBeInTheDocument()
  })
})
