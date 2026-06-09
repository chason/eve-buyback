import { apiGet } from "./client"
import type {
  MarketGroupOut,
  StationSearchResult,
  TypeSearchResult,
} from "./types"

export type {
  MarketGroupOut,
  StationSearchResult,
  TypeSearchResult,
} from "./types"

/** Search SDE types by name. The backend requires a query of at least 2 chars. */
export const searchTypes = (q: string) =>
  apiGet<TypeSearchResult[]>(`/types/search?q=${encodeURIComponent(q)}`)

/** The full market-group hierarchy (for the rule editor's group picker). */
export const listMarketGroups = () =>
  apiGet<MarketGroupOut[]>("/market-groups")

/** Search seeded NPC stations by system or station name (for the hub picker). */
export const searchStations = (q: string) =>
  apiGet<StationSearchResult[]>(`/stations/search?q=${encodeURIComponent(q)}`)
