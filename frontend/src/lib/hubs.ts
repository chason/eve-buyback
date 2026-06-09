// The five NPC trade hubs Fuzzwork serves per-station aggregates for (ADR-0006).
// Any other NPC station is priced from EVE ESI region orders instead (ADR-0028).
export interface HubPreset {
  id: number
  name: string
}

export const FUZZWORK_HUBS: HubPreset[] = [
  { id: 60003760, name: "Jita 4-4" },
  { id: 60008494, name: "Amarr VIII (Oris)" },
  { id: 60011866, name: "Dodixie IX - Moon 20" },
  { id: 60004588, name: "Rens VI - Moon 8" },
  { id: 60005686, name: "Hek VIII - Moon 12" },
]

const HUB_NAMES: Record<number, string> = Object.fromEntries(
  FUZZWORK_HUBS.map((h) => [h.id, h.name]),
)

/** Whether a station id is one of the Fuzzwork-covered hubs (vs ESI-priced). */
export const isFuzzworkHub = (hubId: number): boolean => hubId in HUB_NAMES

/** Friendly station name for a hub id, falling back to the raw id if unknown. */
export const hubName = (hubId: number): string =>
  HUB_NAMES[hubId] ?? `Station ${hubId}`
