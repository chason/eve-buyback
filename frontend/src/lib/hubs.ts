// The five NPC trade hubs Fuzzwork serves per-station aggregates for (ADR-0006).
// The MVP prices at Jita only, but we name any of them if a config points there.
const HUB_NAMES: Record<number, string> = {
  60003760: "Jita 4-4",
  60008494: "Amarr VIII (Oris)",
  60011866: "Dodixie IX - Moon 20",
  60004588: "Rens VI - Moon 8",
  60005686: "Hek VIII - Moon 12",
}

/** Friendly station name for a hub id, falling back to the raw id if unknown. */
export const hubName = (hubId: number): string =>
  HUB_NAMES[hubId] ?? `Station ${hubId}`
