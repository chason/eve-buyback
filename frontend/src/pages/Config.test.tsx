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
    can_open_contract: false,
    is_app_admin: false,
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

  it("shows the active hub prominently and flags unsaved edits (#34)", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)

    renderConfig()

    // The hub in effect is a prominent confirmation line, not muted hint text.
    const active = await screen.findByText(/Pricing at/)
    expect(active).toHaveClass("active-hub")

    const saveBtn = screen.getByRole("button", { name: "Save config" })
    expect(saveBtn).not.toHaveClass("dirty")
    expect(screen.queryByText("Unsaved changes.")).not.toBeInTheDocument()

    // Editing any default marks the form dirty — the required Save is signalled.
    const pct = screen.getByLabelText("Default percentage")
    await u.clear(pct)
    await u.type(pct, "85")

    expect(saveBtn).toHaveClass("dirty")
    expect(screen.getByText("Unsaved changes.")).toBeInTheDocument()
  })

  it("confirms a successful save with an inline acknowledgment (#38)", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)
    vi.mocked(pricingApi.updateConfig).mockResolvedValue(config)

    renderConfig()
    await u.click(await screen.findByRole("button", { name: "Save config" }))

    // An inline confirmation appears by the button — a polite live region, not the
    // old bare, never-dismissed "Saved." paragraph. (It self-clears after a few
    // seconds via a timeout; the unmount cleanup cancels it.)
    const confirm = await screen.findByText(/changes are live/i)
    expect(confirm.closest("[role=status]")).toBeInTheDocument()
  })

  it("explains the pricing knobs with humanized labels and helper text", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)

    renderConfig()

    // Enum options render friendly labels, not the raw values ("buy", "weighted_average").
    expect(
      await screen.findByRole("option", { name: "Buy orders" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("option", { name: "Weighted average" }),
    ).toBeInTheDocument()
    // The most consequential knob — the aggregate — gets a manipulation-resistance hint.
    expect(screen.getByText(/manipulated order can/i)).toBeInTheDocument()
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

  it("shows the connected character in the Corp ESI access panel", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: true,
      character_name: "Capsuleer",
      expired: false,
    })

    renderConfig()

    expect(await screen.findByText(/Connected as/)).toBeInTheDocument()
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

  it("warns when corp ESI access is failing, with the failing-since date", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.getConfig).mockResolvedValue(config)
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: true,
      character_name: "Capsuleer",
      expired: true,
      failed_since: "2026-06-15T00:00:00Z",
    })

    renderConfig()

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent(/is failing/i)
    expect(alert).toHaveTextContent(/reconnect/i)
    // The failing-since timestamp is surfaced (formatted to the local locale).
    expect(alert).toHaveTextContent("2026")
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
