import { apiGet } from "./client"
import type { TypeSearchResult } from "./types"

export type { MarketGroupOut, TypeSearchResult } from "./types"

/** Search SDE types by name. The backend requires a query of at least 2 chars. */
export const searchTypes = (q: string) =>
  apiGet<TypeSearchResult[]>(`/types/search?q=${encodeURIComponent(q)}`)
