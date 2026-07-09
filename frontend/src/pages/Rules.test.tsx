import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as authApi from "../api/auth"
import * as pricingApi from "../api/pricing"
import * as sdeApi from "../api/sde"
import * as structuresApi from "../api/structures"
import type { SessionUser } from "../api/types"
import Rules from "./Rules"

vi.mock("../api/auth")
vi.mock("../api/pricing")
vi.mock("../api/sde")
vi.mock("../api/structures")

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
  beforeEach(() => {
    vi.resetAllMocks()
    // The AddRule hub picker checks structure availability; default to a
    // configured-but-unauthorized server (tests override as needed).
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: false,
      expired: false,
    })
  })

  it("shows each rule's backend-resolved target name", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([
      { target_kind: "market_group", target_id: 1, target_name: "Ore", basis: "buy", percentage: "80", enabled: true, reprocess: true, compressed_only: false, accepted: true },
      { target_kind: "type", target_id: 34, target_name: "Tritanium", basis: null, percentage: "90", enabled: true, reprocess: false, compressed_only: false, accepted: true },
    ])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([])

    renderRules()

    expect(await screen.findByText("Ore")).toBeInTheDocument()
    // A type target shows its name, not "Type 34".
    expect(screen.getByText("Tritanium")).toBeInTheDocument()
    expect(screen.queryByText("Type 34")).not.toBeInTheDocument()

    // Flags render as status chips, not literal yes/no/dash text (#83).
    for (const chip of screen.getAllByText("On")) {
      expect(chip).toHaveClass("status", "status--accepted") // enabled
    }
    // The Ore rule reprocesses → a cyan "info" chip is present.
    expect(
      screen
        .getAllByText("Yes")
        .some((c) => c.classList.contains("status--info")),
    ).toBe(true)
  })

  it("groups rules into collapsible folders by top-level market group", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([
      { market_group_id: 1, parent_id: null, name: "Ships" },
      { market_group_id: 2, parent_id: 1, name: "Frigates" },
      { market_group_id: 10, parent_id: null, name: "Materials" },
    ])
    vi.mocked(pricingApi.listRules).mockResolvedValue([
      // A type two levels under "Ships" → Ships folder.
      { target_kind: "type", target_id: 587, target_name: "Rifter", basis: null, percentage: "90", enabled: true, reprocess: false, compressed_only: false, accepted: true, target_market_group_id: 2 },
      // A market-group rule on "Materials" → Materials folder.
      { target_kind: "market_group", target_id: 10, target_name: "Materials", basis: "buy", percentage: "85", enabled: true, reprocess: false, compressed_only: false, accepted: true, target_market_group_id: 10 },
      // No/unknown group → the "Other" folder.
      { target_kind: "type", target_id: 99, target_name: "Mystery Item", basis: null, percentage: "50", enabled: true, reprocess: false, compressed_only: false, accepted: true, target_market_group_id: null },
    ])

    renderRules()

    // Folder headers (top-level market groups), each a <details> summary. ("Materials"
    // is also a rule target name, so scope the header lookups to the summary.)
    const ships = await screen.findByText("Ships", { selector: "summary" })
    expect(ships).toBeInTheDocument()
    expect(screen.getByText("Materials", { selector: "summary" })).toBeInTheDocument()
    const other = screen.getByText("Other", { selector: "summary" })
    expect(other).toBeInTheDocument()

    // The Rifter (under Ships ▸ Frigates) files in the Ships folder, not its own.
    expect(within(ships.closest("details")!).getByText("Rifter")).toBeInTheDocument()
    // The ungrouped rule lands in "Other".
    expect(
      within(other.closest("details")!).getByText("Mystery Item"),
    ).toBeInTheDocument()
  })

  it("lets a manager add a market-group rule", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([
      { market_group_id: 1, parent_id: null, name: "Standard Ores" },
    ])
    vi.mocked(pricingApi.putRule).mockResolvedValue({
      target_kind: "market_group", target_id: 1, basis: "sell", percentage: "75", enabled: true, reprocess: true, compressed_only: true, accepted: true,
    })

    renderRules()

    await u.selectOptions(await screen.findByLabelText("Target kind"), "market_group")
    await u.type(screen.getByLabelText("Search market group by name"), "standard ores")
    await u.click(await screen.findByText("Standard Ores"))  // an ore branch → eligible
    await u.selectOptions(screen.getByLabelText("Basis"), "sell")
    const pct = screen.getByLabelText("Rule percentage")
    await u.clear(pct)
    await u.type(pct, "75")
    await u.click(screen.getByLabelText("Reprocess (ore → minerals)"))
    await u.click(screen.getByLabelText("Compressed only"))
    await u.click(screen.getByRole("button", { name: "Save rule" }))

    await waitFor(() => expect(pricingApi.putRule).toHaveBeenCalled())
    const call = vi.mocked(pricingApi.putRule).mock.calls[0]
    expect(call[0]).toBe("market_group")
    expect(call[1]).toBe(1)
    expect(call[2]).toEqual({
      basis: "sell",
      percentage: "75",
      enabled: true,
      accepted: true,
      reprocess: true,
      compressed_only: true,
      folder: null,
    })
  })

  it("sends a hub override when one is picked, and shows it in the table", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([
      { target_kind: "type", target_id: 34, target_name: "Tritanium", basis: null, percentage: "90", enabled: true, reprocess: false, compressed_only: false, accepted: true, market_hub_id: "60008494", market_hub_name: "Amarr VIII (Oris) - Emperor Family Academy" },
    ])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([])
    vi.mocked(sdeApi.searchTypes).mockResolvedValue([
      { type_id: 35, name: "Pyerite", market_group_id: 5 },
    ])
    vi.mocked(pricingApi.putRule).mockResolvedValue({
      target_kind: "type", target_id: 35, basis: null, percentage: "90", enabled: true, reprocess: false, compressed_only: false, accepted: true,
    })

    renderRules()

    // The existing rule's hub renders in the table.
    expect(
      await screen.findByText("Amarr VIII (Oris) - Emperor Family Academy"),
    ).toBeInTheDocument()

    // Add a rule with a hub override picked from the presets.
    await u.type(screen.getByLabelText("Search type by name"), "pyer")
    await u.click(await screen.findByText("Pyerite"))
    await u.selectOptions(screen.getByLabelText("Market hub"), "60008494")
    await u.click(screen.getByRole("button", { name: "Save rule" }))

    await waitFor(() => expect(pricingApi.putRule).toHaveBeenCalled())
    expect(vi.mocked(pricingApi.putRule).mock.calls[0][2]).toMatchObject({
      market_hub_id: "60008494",
      market_hub_kind: "npc_station",
      market_hub_name: null,
    })
  })

  it("can mark a rule as not accepted (blacklist)", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([])
    vi.mocked(sdeApi.searchTypes).mockResolvedValue([
      { type_id: 587, name: "Rifter", market_group_id: 5 },
    ])
    vi.mocked(pricingApi.putRule).mockResolvedValue({
      target_kind: "type", target_id: 587, basis: null, percentage: "90", enabled: true, reprocess: false, compressed_only: false, accepted: false,
    })

    renderRules()

    await u.type(await screen.findByLabelText("Search type by name"), "rifter")
    await u.click(await screen.findByText("Rifter"))
    // Uncheck Accept → pricing fields disappear and the rule rejects the item.
    await u.click(screen.getByLabelText("Accept (buy this item)"))
    expect(screen.queryByLabelText("Rule percentage")).not.toBeInTheDocument()
    await u.click(screen.getByRole("button", { name: "Save rule" }))

    await waitFor(() => expect(pricingApi.putRule).toHaveBeenCalled())
    const call = vi.mocked(pricingApi.putRule).mock.calls[0]
    expect(call[0]).toBe("type")
    expect(call[1]).toBe(587)
    expect(call[2].accepted).toBe(false)
  })

  it("filters market groups by leaf name and shows the full path", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([
      { market_group_id: 10, parent_id: null, name: "Manufacture & Research" },
      { market_group_id: 11, parent_id: 10, name: "Materials" },
      { market_group_id: 12, parent_id: 11, name: "Standard Ores" },
    ])

    renderRules()

    await u.selectOptions(
      await screen.findByLabelText("Target kind"),
      "market_group",
    )
    await u.type(
      screen.getByLabelText("Search market group by name"),
      "standard ores",
    )
    // The match is shown with its full disambiguating path.
    expect(
      await screen.findByText(
        "Manufacture & Research / Materials / Standard Ores",
      ),
    ).toBeInTheDocument()
  })

  it("matches a parent term and lists its descendants too", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([
      { market_group_id: 10, parent_id: null, name: "Manufacture & Research" },
      { market_group_id: 11, parent_id: 10, name: "Materials" },
      { market_group_id: 12, parent_id: 11, name: "Standard Ores" },
    ])

    renderRules()

    await u.selectOptions(
      await screen.findByLabelText("Target kind"),
      "market_group",
    )
    // "Materials" is a parent; searching it surfaces the group itself AND its
    // descendant "Standard Ores" (whose path contains "Materials").
    await u.type(screen.getByLabelText("Search market group by name"), "materials")
    expect(
      await screen.findByText("Manufacture & Research / Materials"),
    ).toBeInTheDocument()
    expect(
      screen.getByText("Manufacture & Research / Materials / Standard Ores"),
    ).toBeInTheDocument()
  })

  it("offers Reprocess only for ore targets", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(pricingApi.listRules).mockResolvedValue([])
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([
      { market_group_id: 1, parent_id: null, name: "Standard Ores" },
      { market_group_id: 2, parent_id: null, name: "Ship Equipment" },
    ])

    renderRules()

    await u.selectOptions(
      await screen.findByLabelText("Target kind"),
      "market_group",
    )
    const box = screen.getByLabelText("Search market group by name")

    // A non-ore group → no Reprocess checkbox and no Compressed-only hint.
    await u.type(box, "ship equipment")
    await u.click(await screen.findByText("Ship Equipment"))
    expect(
      screen.queryByLabelText("Reprocess (ore → minerals)"),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText(/rejects the uncompressed variants/i),
    ).not.toBeInTheDocument()

    // Switch to an ore branch → the checkbox and its explanatory hint appear.
    await u.clear(box)
    await u.type(box, "standard ores")
    await u.click(await screen.findByText("Standard Ores"))
    expect(
      screen.getByLabelText("Reprocess (ore → minerals)"),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/rejects the uncompressed variants/i),
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

  it("groups by custom folder under 'My folders', with Ungrouped last (ADR-0039)", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([])
    vi.mocked(pricingApi.listRules).mockResolvedValue([
      { target_kind: "type", target_id: 34, target_name: "Tritanium", basis: null, percentage: "90", enabled: true, reprocess: false, compressed_only: false, accepted: true, folder: "Moon goo" },
      { target_kind: "type", target_id: 35, target_name: "Pyerite", basis: null, percentage: "90", enabled: true, reprocess: false, compressed_only: false, accepted: true },
    ])

    renderRules()

    await screen.findByText("Tritanium")
    // Category view (default) shows no custom-folder headers.
    expect(screen.queryByText("Moon goo")).not.toBeInTheDocument()

    await u.click(screen.getByRole("button", { name: "My folders" }))
    // The custom folder + the Ungrouped bucket (for the unfiled rule) appear.
    expect(screen.getByText("Moon goo")).toBeInTheDocument()
    expect(screen.getByText("Ungrouped")).toBeInTheDocument()
  })

  it("edits a rule: pre-fills the form incl. folder and re-saves it (ADR-0039)", async () => {
    const u = userEvent.setup()
    vi.mocked(authApi.getMe).mockResolvedValue(user("manager"))
    vi.mocked(sdeApi.listMarketGroups).mockResolvedValue([])
    vi.mocked(pricingApi.putRule).mockResolvedValue({} as never)
    vi.mocked(pricingApi.listRules).mockResolvedValue([
      { target_kind: "type", target_id: 34, target_name: "Tritanium", basis: "buy", percentage: "85", enabled: true, reprocess: false, compressed_only: false, accepted: true, folder: "Moon goo" },
    ])

    renderRules()

    await u.click(await screen.findByRole("button", { name: "Edit" }))

    // Edit mode: header, locked target, and pre-filled fields.
    expect(screen.getByText(/Edit rule — Tritanium/)).toBeInTheDocument()
    const folder = screen.getByLabelText("Rule folder") as HTMLInputElement
    expect(folder.value).toBe("Moon goo")
    expect(
      (screen.getByLabelText("Rule percentage") as HTMLInputElement).value,
    ).toBe("85")

    await u.clear(folder)
    await u.type(folder, "Reactions")
    await u.click(screen.getByRole("button", { name: "Save changes" }))

    await waitFor(() => expect(pricingApi.putRule).toHaveBeenCalled())
    const [kind, id, body] = vi.mocked(pricingApi.putRule).mock.calls[0]
    expect([kind, id, body.folder]).toEqual(["type", 34, "Reactions"])
  })
})
