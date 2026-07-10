import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as adminApi from "../api/admin"
import * as authApi from "../api/auth"
import type { CorpAccessOut, SessionUser } from "../api/types"
import Admin from "./Admin"

vi.mock("../api/auth")
vi.mock("../api/admin")

function user(over: Partial<SessionUser> = {}): SessionUser {
  return {
    character_id: 1,
    character_name: "Operator",
    corporation_id: 2,
    corporation_name: "Op Corp",
    role: "member",
    is_director: false,
    corporation_registered: true,
    can_open_contract: false,
    is_app_admin: true,
    ...over,
  }
}

function corp(over: Partial<CorpAccessOut> = {}): CorpAccessOut {
  return {
    corporation_id: 98000001,
    corporation_name: "Test Corp",
    active: false,
    source: null,
    granted_at: null,
    expires_at: null,
    granted_by_character_id: null,
    ...over,
  }
}

function renderAdmin() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Admin />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function payment(over: Partial<adminApi.PaymentOut> = {}): adminApi.PaymentOut {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    // Distinct from the default price so amount assertions never collide with the
    // price editor's text.
    amount: "300000000.00",
    sender_name: "Rich Buyer",
    reason: "forgot the reference",
    received_at: "2026-07-10T00:00:00Z",
    matched: false,
    matched_corporation_name: null,
    periods_granted: 0,
    ...over,
  }
}

describe("Admin", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(authApi.getMe).mockResolvedValue(user())
    vi.mocked(adminApi.listCorpAccess).mockResolvedValue([corp()])
    vi.mocked(adminApi.getWalletStatus).mockResolvedValue({
      configured: true,
      connected: false,
      character_name: null,
      expired: false,
      created_at: null,
    })
    vi.mocked(adminApi.listPayments).mockResolvedValue([])
    vi.mocked(adminApi.getBillingSettings).mockResolvedValue({
      price_isk: 250_000_000,
      period_days: 30,
    })
  })

  it("blocks a non-admin", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(user({ is_app_admin: false }))
    renderAdmin()
    expect(
      await screen.findByText(/Only an app admin/i),
    ).toBeInTheDocument()
    expect(adminApi.listCorpAccess).not.toHaveBeenCalled()
  })

  it("lists corps with plain-English access status", async () => {
    vi.mocked(adminApi.listCorpAccess).mockResolvedValue([
      corp(),
      corp({
        corporation_id: 98000002,
        corporation_name: "Rich Corp",
        active: true,
        source: "admin",
        granted_at: "2026-07-01T00:00:00Z",
      }),
    ])
    renderAdmin()
    expect(await screen.findByText("Test Corp")).toBeInTheDocument()
    expect(screen.getByText("Off")).toBeInTheDocument()
    expect(screen.getByText("On")).toBeInTheDocument()
    expect(screen.getByText("Forever")).toBeInTheDocument() // active, no expiry
    expect(screen.getByText(/granted by admin/)).toBeInTheDocument()
  })

  it("gives access forever when no date is picked", async () => {
    const u = userEvent.setup()
    vi.mocked(adminApi.grantCorpAccess).mockResolvedValue(
      corp({ active: true, source: "admin", granted_at: "2026-07-09T00:00:00Z" }),
    )
    renderAdmin()
    await u.click(await screen.findByRole("button", { name: "Give access" }))
    await waitFor(() =>
      expect(adminApi.grantCorpAccess).toHaveBeenCalledWith(98000001, null),
    )
  })

  it("gives access until a picked date (end of day, UTC)", async () => {
    const u = userEvent.setup()
    vi.mocked(adminApi.grantCorpAccess).mockResolvedValue(
      corp({ active: true, granted_at: "2026-07-09T00:00:00Z" }),
    )
    renderAdmin()
    const date = await screen.findByLabelText(/Access until date for Test Corp/)
    await u.type(date, "2026-08-09")
    await u.click(screen.getByRole("button", { name: "Give access" }))
    await waitFor(() =>
      expect(adminApi.grantCorpAccess).toHaveBeenCalledWith(
        98000001,
        "2026-08-09T23:59:59Z",
      ),
    )
  })

  it("removes access behind a confirm", async () => {
    const u = userEvent.setup()
    vi.mocked(adminApi.listCorpAccess).mockResolvedValue([
      corp({ active: true, source: "admin", granted_at: "2026-07-01T00:00:00Z" }),
    ])
    vi.mocked(adminApi.revokeCorpAccess).mockResolvedValue(undefined)
    renderAdmin()
    await u.click(await screen.findByRole("button", { name: "Remove" }))
    // Destructive → confirm popup first (#36).
    expect(adminApi.revokeCorpAccess).not.toHaveBeenCalled()
    await u.click(screen.getByRole("button", { name: "Remove access" }))
    await waitFor(() =>
      expect(adminApi.revokeCorpAccess).toHaveBeenCalledWith(98000001),
    )
  })

  it("lets the admin change the access price (ADR-0042)", async () => {
    const u = userEvent.setup()
    vi.mocked(adminApi.updateBillingSettings).mockResolvedValue({
      price_isk: 100_000_000,
      period_days: 30,
    })
    renderAdmin()

    // The current price reads in plain English…
    expect(await screen.findByText("250,000,000.00 ISK")).toBeInTheDocument()
    const save = screen.getByRole("button", { name: "Save price" })
    expect(save).toBeDisabled() // unchanged value → nothing to save

    // …and editing it enables Save, which sends the new price.
    const input = screen.getByLabelText("Access price in ISK")
    await u.clear(input)
    await u.type(input, "100000000")
    await u.click(save)
    await waitFor(() =>
      expect(adminApi.updateBillingSettings).toHaveBeenCalledWith(100_000_000),
    )
  })

  it("offers to connect the payment wallet when none is connected", async () => {
    renderAdmin()
    expect(
      await screen.findByRole("button", { name: "Connect payment wallet" }),
    ).toBeInTheDocument()
    // No wallet → no payment polling.
    expect(adminApi.listPayments).not.toHaveBeenCalled()
  })

  it("applies an unmatched payment to a picked corporation", async () => {
    const u = userEvent.setup()
    vi.mocked(adminApi.getWalletStatus).mockResolvedValue({
      configured: true,
      connected: true,
      character_name: "Site Operator",
      expired: false,
      created_at: "2026-07-01T00:00:00Z",
    })
    vi.mocked(adminApi.listPayments).mockResolvedValue([payment()])
    vi.mocked(adminApi.matchPayment).mockResolvedValue(
      payment({ matched: true, matched_corporation_name: "Test Corp" }),
    )
    renderAdmin()

    // The unmatched payment shows sender, ISK amount, and the transfer message.
    expect(await screen.findByText("Rich Buyer")).toBeInTheDocument()
    expect(screen.getByText("300,000,000.00 ISK")).toBeInTheDocument()
    expect(screen.getByText("forgot the reference")).toBeInTheDocument()

    // The corp picker fills from the (separately fetched) access list.
    await screen.findByRole("option", { name: "Test Corp" })
    await u.selectOptions(
      screen.getByLabelText(/Corporation for payment/),
      "98000001",
    )
    await u.click(screen.getByRole("button", { name: "Apply" }))
    await waitFor(() =>
      expect(adminApi.matchPayment).toHaveBeenCalledWith(
        "11111111-1111-1111-1111-111111111111",
        98000001,
      ),
    )
  })

  it("shows matched payments with the corp they unlocked", async () => {
    vi.mocked(adminApi.getWalletStatus).mockResolvedValue({
      configured: true,
      connected: true,
      character_name: "Site Operator",
      expired: false,
      created_at: "2026-07-01T00:00:00Z",
    })
    vi.mocked(adminApi.listPayments).mockResolvedValue([
      payment({
        matched: true,
        matched_corporation_name: "Paying Corp",
        periods_granted: 2,
      }),
    ])
    renderAdmin()
    expect(await screen.findByText("Paying Corp")).toBeInTheDocument()
    expect(screen.getByText(/\+60 days/)).toBeInTheDocument()
  })
})
