import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { SessionUser } from "../api/auth"
import * as authApi from "../api/auth"
import * as versionApi from "../api/version"
import Layout from "./Layout"

vi.mock("../api/auth")
vi.mock("../api/version")

function member(): SessionUser {
  return {
    character_id: 1,
    character_name: "Boss",
    corporation_id: 2,
    corporation_name: "Corp",
    corporation_registered: true,
    is_director: false,
    role: "member",
  }
}

function renderAt(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/appraise" element={<div />} />
            <Route path="/appraisals" element={<div />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Layout nav", () => {
  beforeEach(() => vi.resetAllMocks())

  it("marks only the active route and renders the character as an identity tag", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(member())
    vi.mocked(versionApi.getVersion).mockResolvedValue({ version: "1" })

    renderAt("/appraise")

    // The active route is flagged (NavLink sets aria-current="page")…
    const active = await screen.findByRole("link", { name: "Appraise" })
    expect(active).toHaveAttribute("aria-current", "page")
    // …and a sibling route isn't.
    expect(
      screen.getByRole("link", { name: "Appraisals" }),
    ).not.toHaveAttribute("aria-current")

    // The signed-in character renders as the muted identity tag, not a link.
    expect(screen.getByText("Boss")).toHaveClass("identity")
  })

  it("shows the live EVE/UTC clock in the footer (#114)", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(member())
    vi.mocked(versionApi.getVersion).mockResolvedValue({ version: "1" })

    renderAt("/appraise")

    const clock = await screen.findByTitle("Current EVE time")
    expect(clock).toHaveTextContent(/^EVE Time \d{2}:\d{2}:\d{2}$/)
  })

  it("links to the privacy page from the footer (#112)", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(member())
    vi.mocked(versionApi.getVersion).mockResolvedValue({ version: "1" })

    renderAt("/appraise")

    const link = await screen.findByRole("link", { name: /privacy/i })
    expect(link).toHaveAttribute("href", "/privacy")

    // …and to the open-source repo (#112).
    const source = screen.getByRole("link", { name: /source/i })
    expect(source).toHaveAttribute("href", "https://github.com/chason/eve-buyback")
    expect(source).toHaveAttribute("target", "_blank")
  })

  it("exposes Log out as a button, not a link (#80)", async () => {
    vi.mocked(authApi.getMe).mockResolvedValue(member())
    vi.mocked(versionApi.getVersion).mockResolvedValue({ version: "1" })

    renderAt("/appraise")

    // It's an action, so it must carry button — not link — semantics for AT/keyboard.
    expect(
      await screen.findByRole("button", { name: "Log out" }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Log out" }),
    ).not.toBeInTheDocument()
  })
})
