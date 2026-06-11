import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as authApi from "../api/auth"
import * as pricingApi from "../api/pricing"
import * as sdeApi from "../api/sde"
import * as structuresApi from "../api/structures"
import type { SessionUser } from "../api/types"
import Config from "./Config"

vi.mock("../api/auth")
vi.mock("../api/pricing")
vi.mock("../api/sde")
vi.mock("../api/structures")

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

function renderConfig(initialEntries: string[] = ["/config"]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <Config />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Config", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    // Managers fetch the structure status to decide structure availability; default
    // to a configured server with no authorization (tests override as needed).
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: false,
      expired: false,
    })
  })

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
      market_hub_name: null,
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

  it("keeps the structure hub selected after returning from authorization", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    // Saved config still points at Jita — the structure isn't picked/saved yet.
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: true,
      character_name: "Capsuleer",
      expired: false,
    })

    // Callback redirects here with this param after the SSO round-trip.
    renderConfig(["/config?authorized=structure"])

    const hub = (await screen.findByLabelText("Market hub")) as HTMLSelectElement
    // Picker stays on "Player structure" instead of snapping back to Jita…
    await waitFor(() => expect(hub.value).toBe("structure"))
    // …and the connected status resolves (no longer the not-authorized prompt).
    expect(await screen.findByText(/connected as/)).toBeInTheDocument()
    expect(screen.getByText("Capsuleer")).toBeInTheDocument()
  })

  it("disables the structure option when the server has no encryption key", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: false,
      authorized: false,
      expired: false,
    })

    renderConfig()

    const option = (await screen.findByRole("option", {
      name: /not available on this server/i,
    })) as HTMLOptionElement
    expect(option.disabled).toBe(true)
  })

  it("warns when re-auth switched to a different character", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: true,
      character_name: "Alt Pilot",
      expired: false,
    })

    // Callback passes the previous character name when the picker swapped it.
    renderConfig(["/config?authorized=structure&replaced=Old%20Pilot"])

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent(/switched from/i)
    expect(alert).toHaveTextContent("Old Pilot")
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
