import { apiGet, apiSend } from "./client"
import type { LocationCreateRequest, LocationOut } from "./types"

export type { LocationCreateRequest, LocationOut, LocationKind } from "./types"

/** The corp's accepted buyback drop-off locations (ADR-0030). Any member may read. */
export const listLocations = () =>
  apiGet<LocationOut[]>("/corporations/me/locations")

/** Add an accepted drop-off location (manager only). */
export async function addLocation(
  body: LocationCreateRequest,
): Promise<LocationOut> {
  const res = await apiSend("POST", "/corporations/me/locations", body)
  if (!res.ok) throw new Error(`Add location failed: ${res.status}`)
  return (await res.json()) as LocationOut
}

/** Remove an accepted drop-off location (manager only). */
export async function removeLocation(locationId: string): Promise<void> {
  const res = await apiSend(
    "DELETE",
    `/corporations/me/locations/${encodeURIComponent(locationId)}`,
  )
  if (!res.ok) throw new Error(`Remove location failed: ${res.status}`)
}
