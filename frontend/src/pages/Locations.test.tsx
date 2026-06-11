import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import * as authApi from "../api/auth"
import * as locationsApi from "../api/locations"
import * as sdeApi from "../api/sde"
import * as structuresApi from "../api/structures"
import type { SessionUser } from "../api/types"
import Locations from "./Locations"

vi.mock("../api/auth")
vi.mock("../api/locations")
vi.mock("../api/sde")
vi.mock("../api/structures")

function manager(): SessionUser {
  return {
    character_id: 1,
    character_name: "Boss",
    corporation_id: 2,
    corporation_name: "Corp",
    role: "manager",
    is_director: false,
    corporation_registered: true,
  }
}

function renderLocations() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Locations />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Locations", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(authApi.getMe).mockResolvedValue(manager())
    vi.mocked(locationsApi.listLocations).mockResolvedValue([])
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: true,
      authorized: false,
      expired: false,
    })
  })

  it("adds an NPC station picked from the search list", async () => {
    const user = userEvent.setup()
    vi.mocked(sdeApi.searchStations).mockResolvedValue([
      {
        station_id: 60003760,
        name: "Jita IV - Moon 4 - Caldari Navy Assembly Plant",
        system_name: "Jita",
        region_id: 10000002,
      },
    ])
    vi.mocked(locationsApi.addLocation).mockResolvedValue({
      location_id: "60003760",
      kind: "npc_station",
      name: "Jita - Jita IV - Moon 4 - Caldari Navy Assembly Plant",
    })

    renderLocations()

    await user.type(await screen.findByLabelText("Search station"), "jita")
    await user.click(await screen.findByText(/Jita - Jita IV/))

    await waitFor(() => expect(locationsApi.addLocation).toHaveBeenCalled())
    expect(vi.mocked(locationsApi.addLocation).mock.calls[0][0]).toEqual({
      location_id: "60003760",
      kind: "npc_station",
    })
  })

  it("prompts to authorize before adding a structure", async () => {
    renderLocations()
    // Unauthorized → no structure search box, just the authorize hint.
    expect(
      await screen.findByText(/authorize structure access/i),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Search structure")).not.toBeInTheDocument()
  })

  it("explains structures are unavailable when the server has no key", async () => {
    vi.mocked(structuresApi.getStructureStatus).mockResolvedValue({
      configured: false,
      authorized: false,
      expired: false,
    })
    renderLocations()
    expect(
      await screen.findByText(/not available on this server/i),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Search structure")).not.toBeInTheDocument()
  })
})
