import { apiGet } from "./client"
import type { MarketGroupOut, TypeSearchResult } from "./types"

export type { MarketGroupOut, TypeSearchResult } from "./types"

/** Search SDE types by name. The backend requires a query of at least 2 chars. */
export const searchTypes = (q: string) =>
  apiGet<TypeSearchResult[]>(`/types/search?q=${encodeURIComponent(q)}`)

/** The full market-group hierarchy (for the rule editor's group picker). */
export const listMarketGroups = () =>
  apiGet<MarketGroupOut[]>("/market-groups")
