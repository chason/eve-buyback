import { useQuery } from "@tanstack/react-query"
import { type ReactNode, useState } from "react"

import { searchStations } from "../api/sde"
import { getStructureStatus, searchStructures } from "../api/structures"
import type { HubKind } from "../api/types"
import { FUZZWORK_HUBS, isFuzzworkHub } from "../lib/hubs"

const CUSTOM = "custom" // "other NPC station" (searchable)
const STRUCTURE = "structure" // player structure (authenticated ESI)
const DEFAULT = "default" // the optional leading "(corp default)" choice

/** What the picker currently resolves to. "default" only occurs when the picker was
 * given a `defaultOption`; "incomplete" means a searchable kind is chosen but nothing
 * picked yet (parents should block submission). */
export type HubSelection =
  | { state: "default" }
  | { state: "incomplete" }
  | { state: "hub"; hubId: string; kind: HubKind; name: string | null }

interface Picked {
  id: string
  label: string
}

interface HubPickerProps {
  onChange: (selection: HubSelection) => void
  disabled?: boolean
  /** Label for a leading "inherit" option (e.g. "(corp default)" in the rule
   * editor). Omit it where a concrete hub is required (the corp config). */
  defaultOption?: string
  /** The currently-saved hub to seed the picker with; null selects the default
   * option (or the first preset when there is none). */
  initial?: { hubId: string; kind: HubKind; name: string | null } | null
  /** Open on "Player structure" even when `initial` isn't one — used by Config
   * right after the structure-authorization round-trip. */
  forceStructureChoice?: boolean
  /** Rendered above the structure search when "Player structure" is chosen —
   * Config injects its authorize/revoke panel here. Without it, an unauthorized
   * user gets a hint pointing at the Config page. */
  structureSlot?: ReactNode
}

/** The market-hub selector shared by the corp config and the rule editor
 * (ADR-0028/0029/0031): Fuzzwork presets, any NPC station (SDE search), or a player
 * structure (authenticated search; disabled when the server has no encryption key).
 * Selection state lives here; authorization UX belongs to the parent. */
export default function HubPicker({
  onChange,
  disabled,
  defaultOption,
  initial,
  forceStructureChoice,
  structureSlot,
}: HubPickerProps) {
  const [choice, setChoice] = useState<string>(() => {
    if (forceStructureChoice) return STRUCTURE
    if (!initial) return defaultOption ? DEFAULT : FUZZWORK_HUBS[0].id
    if (initial.kind === "structure") return STRUCTURE
    if (isFuzzworkHub(initial.hubId)) return initial.hubId
    return CUSTOM
  })
  const [stationQuery, setStationQuery] = useState("")
  const [station, setStation] = useState<Picked | null>(() =>
    initial && initial.kind !== "structure" && !isFuzzworkHub(initial.hubId)
      ? { id: initial.hubId, label: initial.name ?? `Station ${initial.hubId}` }
      : null,
  )
  const [structureQuery, setStructureQuery] = useState("")
  const [structure, setStructure] = useState<Picked | null>(() =>
    initial && initial.kind === "structure"
      ? { id: initial.hubId, label: initial.name ?? `Structure ${initial.hubId}` }
      : null,
  )

  // Availability/authorization for the structure option (shared query key —
  // React Query dedupes with the parent's own status query).
  const structureStatus = useQuery({
    queryKey: ["structureStatus"],
    queryFn: getStructureStatus,
    enabled: !disabled,
  })
  const structuresAvailable = structureStatus.data?.configured !== false
  const authorized = !!structureStatus.data?.authorized

  const query = stationQuery.trim()
  const stationResults = useQuery({
    queryKey: ["stations", query],
    queryFn: () => searchStations(query),
    enabled: choice === CUSTOM && query.length >= 2,
  })
  const sQuery = structureQuery.trim()
  const structureResults = useQuery({
    queryKey: ["structures", sQuery],
    queryFn: () => searchStructures(sQuery),
    enabled: choice === STRUCTURE && authorized && sQuery.length >= 3,
  })

  function emit(
    nextChoice: string,
    nextStation: Picked | null,
    nextStructure: Picked | null,
  ) {
    if (nextChoice === DEFAULT) {
      onChange({ state: "default" })
    } else if (nextChoice === STRUCTURE) {
      onChange(
        nextStructure
          ? {
              state: "hub",
              hubId: nextStructure.id,
              kind: "structure",
              name: nextStructure.label,
            }
          : { state: "incomplete" },
      )
    } else if (nextChoice === CUSTOM) {
      onChange(
        nextStation
          ? {
              state: "hub",
              hubId: nextStation.id,
              kind: "npc_station",
              name: nextStation.label,
            }
          : { state: "incomplete" },
      )
    } else {
      onChange({
        state: "hub",
        hubId: nextChoice,
        kind: "npc_station",
        name: null,
      })
    }
  }

  return (
    <>
      <label>
        Market hub
        <select
          value={choice}
          disabled={disabled}
          onChange={(e) => {
            setChoice(e.target.value)
            emit(e.target.value, station, structure)
          }}
          aria-label="Market hub"
        >
          {defaultOption && <option value={DEFAULT}>{defaultOption}</option>}
          {FUZZWORK_HUBS.map((h) => (
            <option key={h.id} value={h.id}>
              {h.name}
            </option>
          ))}
          <option value={CUSTOM}>Other NPC station…</option>
          <option value={STRUCTURE} disabled={!structuresAvailable}>
            {structuresAvailable
              ? "Player structure…"
              : "Player structure… (not available on this server)"}
          </option>
        </select>
      </label>

      {choice === CUSTOM && (
        <label>
          Station
          <input
            type="search"
            value={station ? station.label : stationQuery}
            placeholder="Search by system or station…"
            aria-label="Search station"
            disabled={disabled}
            onChange={(e) => {
              setStation(null)
              setStationQuery(e.target.value)
              emit(CUSTOM, null, structure)
            }}
          />
          {!station && query.length >= 2 && (
            <ul className="search-results">
              {stationResults.isLoading && <li aria-busy="true">Searching…</li>}
              {stationResults.data?.map((s) => (
                <li key={s.station_id}>
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault()
                      const picked = {
                        id: String(s.station_id),
                        label: `${s.system_name} - ${s.name}`,
                      }
                      setStation(picked)
                      setStationQuery("")
                      emit(CUSTOM, picked, structure)
                    }}
                  >
                    {s.system_name} - {s.name}
                  </a>
                </li>
              ))}
              {stationResults.data?.length === 0 && <li>No matches.</li>}
            </ul>
          )}
          <small className="field-hint">
            Any NPC station — priced live from EVE ESI region orders.
          </small>
        </label>
      )}

      {choice === STRUCTURE && !structuresAvailable && (
        <p className="error">
          Player-structure pricing isn&apos;t available on this server — the
          operator hasn&apos;t configured the token-encryption key
          (BUYBACK_TOKEN_ENCRYPTION_KEY). Pick another hub.
        </p>
      )}

      {choice === STRUCTURE && structuresAvailable && (
        <>
          {structureSlot ??
            (!authorized && (
              <small className="field-hint">
                Authorize structure access on the Config page first.
              </small>
            ))}

          {authorized && (
            <label>
              Structure
              <input
                type="search"
                value={structure ? structure.label : structureQuery}
                placeholder="Search structures by name…"
                aria-label="Search structure"
                disabled={disabled}
                onChange={(e) => {
                  setStructure(null)
                  setStructureQuery(e.target.value)
                  emit(STRUCTURE, station, null)
                }}
              />
              {!structure && sQuery.length >= 3 && (
                <ul className="search-results">
                  {structureResults.isLoading && (
                    <li aria-busy="true">Searching…</li>
                  )}
                  {structureResults.isError && (
                    <li className="error">
                      Search failed — your structure access may have expired.
                      Re-authorize and try again.
                    </li>
                  )}
                  {structureResults.data?.map((s) => (
                    <li key={s.structure_id}>
                      <a
                        href="#"
                        onClick={(e) => {
                          e.preventDefault()
                          const picked = { id: s.structure_id, label: s.name }
                          setStructure(picked)
                          setStructureQuery("")
                          emit(STRUCTURE, station, picked)
                        }}
                      >
                        {s.name}
                      </a>
                    </li>
                  ))}
                  {structureResults.data?.length === 0 && <li>No matches.</li>}
                </ul>
              )}
              <small className="field-hint">
                Only structures your authorized character can dock at appear.
              </small>
            </label>
          )}
        </>
      )}
    </>
  )
}
