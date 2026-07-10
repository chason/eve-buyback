import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as adminApi from "../api/admin"
import * as authApi from "../api/auth"
import * as structuresApi from "../api/structures"
import Callback from "./Callback"

vi.mock("../api/auth")
// Keep the real state-prefix routing constants; stub only the network calls so we
// can assert which completion the callback dispatches to.
vi.mock("../api/structures", async (importActual) => ({
  ...(await importActual<typeof structuresApi>()),
  completeStructureAuthorize: vi.fn(),
}))
vi.mock("../api/admin", async (importActual) => ({
  ...(await importActual<typeof adminApi>()),
  completeWalletAuthorize: vi.fn(),
}))

function renderAt(query: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/auth/callback${query}`]}>
        <Callback />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Callback routing", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(authApi.login).mockResolvedValue({} as never)
    vi.mocked(structuresApi.completeStructureAuthorize).mockResolvedValue(
      {} as never,
    )
    vi.mocked(adminApi.completeWalletAuthorize).mockResolvedValue({} as never)
  })

  it("routes a structure-prefixed state to the structure completion", async () => {
    renderAt("?code=abc&state=structure.xyz")
    await waitFor(() =>
      expect(structuresApi.completeStructureAuthorize).toHaveBeenCalledWith(
        "abc",
        "structure.xyz",
      ),
    )
    expect(authApi.login).not.toHaveBeenCalled()
  })

  it("routes an opwallet-prefixed state to the wallet completion (ADR-0042)", async () => {
    renderAt("?code=abc&state=opwallet.xyz")
    await waitFor(() =>
      expect(adminApi.completeWalletAuthorize).toHaveBeenCalledWith(
        "abc",
        "opwallet.xyz",
      ),
    )
    expect(authApi.login).not.toHaveBeenCalled()
    expect(structuresApi.completeStructureAuthorize).not.toHaveBeenCalled()
  })

  it("routes a plain login state to the login completion (not structure)", async () => {
    renderAt("?code=abc&state=plain-login-state")
    await waitFor(() =>
      expect(authApi.login).toHaveBeenCalledWith("abc", "plain-login-state"),
    )
    expect(structuresApi.completeStructureAuthorize).not.toHaveBeenCalled()
    expect(adminApi.completeWalletAuthorize).not.toHaveBeenCalled()
  })
})
