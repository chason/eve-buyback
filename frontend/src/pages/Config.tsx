import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { getMe } from "../api/auth"
import { getConfig, updateConfig } from "../api/pricing"
import { searchStations } from "../api/sde"
import {
  beginStructureAuthorize,
  getStructureStatus,
  revokeStructure,
  searchStructures,
  STRUCTURE_AUTH_FLAG,
} from "../api/structures"
import type { AggregateField, Basis } from "../api/types"
import { FUZZWORK_HUBS, hubName, isFuzzworkHub } from "../lib/hubs"
import { isManager } from "../lib/roles"

const CUSTOM = "custom" // "other NPC station" (searchable)
const STRUCTURE = "structure" // player structure (authenticated ESI)
const BASES: Basis[] = ["buy", "sell", "split"]
const AGGREGATES: AggregateField[] = [
  "percentile",
  "weighted_average",
  "median",
  "max",
  "min",
]

export default function Config() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig })
  const canEdit = isManager(me.data?.role)

  const [basis, setBasis] = useState<Basis>("buy")
  const [percentage, setPercentage] = useState("90")
  const [aggregate, setAggregate] = useState<AggregateField>("percentile")
  const [defaultAccepted, setDefaultAccepted] = useState(true)
  // Hub picker: a Fuzzwork preset id, CUSTOM (search an NPC station), or STRUCTURE.
  const [hubChoice, setHubChoice] = useState<string>(FUZZWORK_HUBS[0].id)
  const [stationQuery, setStationQuery] = useState("")
  const [station, setStation] = useState<{ id: string; label: string } | null>(
    null,
  )
  const [structureQuery, setStructureQuery] = useState("")
  const [structure, setStructure] = useState<{ id: string; label: string } | null>(
    null,
  )

  // Seed the form once the config loads (and after a save refetch).
  useEffect(() => {
    if (config.data) {
      setBasis(config.data.default_basis)
      setPercentage(config.data.default_percentage)
      setAggregate(config.data.aggregate_field)
      setDefaultAccepted(config.data.default_accepted)
      const hub = config.data.market_hub_id
      if (config.data.market_hub_kind === "structure") {
        setHubChoice(STRUCTURE)
        setStructure({
          id: hub,
          label: config.data.market_hub_name ?? `Structure ${hub}`,
        })
      } else if (isFuzzworkHub(hub)) {
        setHubChoice(hub)
      } else {
        setHubChoice(CUSTOM)
        setStation({ id: hub, label: config.data.market_hub_name ?? `Station ${hub}` })
      }
    }
  }, [config.data])

  const query = stationQuery.trim()
  const stationResults = useQuery({
    queryKey: ["stations", query],
    queryFn: () => searchStations(query),
    enabled: hubChoice === CUSTOM && query.length >= 2,
  })
  const structureStatus = useQuery({
    queryKey: ["structureStatus"],
    queryFn: getStructureStatus,
    enabled: hubChoice === STRUCTURE,
  })
  const authorized = !!structureStatus.data?.authorized
  const sQuery = structureQuery.trim()
  const structureResults = useQuery({
    queryKey: ["structures", sQuery],
    queryFn: () => searchStructures(sQuery),
    enabled: hubChoice === STRUCTURE && authorized && sQuery.length >= 3,
  })

  const hubKind = hubChoice === STRUCTURE ? "structure" : "npc_station"
  const hubId =
    hubChoice === STRUCTURE
      ? (structure?.id ?? "")
      : hubChoice === CUSTOM
        ? (station?.id ?? "")
        : hubChoice
  const hubInvalid = hubId === ""

  const save = useMutation({
    mutationFn: () =>
      updateConfig({
        market_hub_id: hubId,
        market_hub_kind: hubKind,
        default_basis: basis,
        default_percentage: percentage,
        aggregate_field: aggregate,
        default_accepted: defaultAccepted,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["config"] }),
  })

  const revoke = useMutation({
    mutationFn: revokeStructure,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["structureStatus"] }),
  })

  async function startStructureAuthorize() {
    const { authorization_url } = await beginStructureAuthorize()
    sessionStorage.setItem(STRUCTURE_AUTH_FLAG, "1")
    window.location.href = authorization_url
  }

  if (config.isLoading) return <p aria-busy="true">Loading…</p>
  if (config.isError || !config.data) {
    return <p className="error">Could not load the buyback config.</p>
  }

  return (
    <>
      <hgroup>
        <h1>Buyback config</h1>
        <p>The corp-wide defaults applied when no rule overrides an item.</p>
      </hgroup>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <label>
          Market hub
          <select
            value={hubChoice}
            disabled={!canEdit}
            onChange={(e) => setHubChoice(e.target.value)}
            aria-label="Market hub"
          >
            {FUZZWORK_HUBS.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name}
              </option>
            ))}
            <option value={CUSTOM}>Other NPC station…</option>
            <option value={STRUCTURE}>Player structure…</option>
          </select>
        </label>

        {hubChoice === CUSTOM && (
          <label>
            Station
            <input
              type="search"
              value={station ? station.label : stationQuery}
              placeholder="Search by system or station…"
              aria-label="Search station"
              disabled={!canEdit}
              onChange={(e) => {
                setStation(null)
                setStationQuery(e.target.value)
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
                        setStation({
                          id: String(s.station_id),
                          label: `${s.system_name} - ${s.name}`,
                        })
                        setStationQuery("")
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

        {hubChoice === STRUCTURE && (
          <>
            <article>
              {authorized ? (
                <p>
                  Structure access: connected as{" "}
                  <strong>{structureStatus.data?.character_name}</strong>
                  {structureStatus.data?.expired && (
                    <span className="error"> — expired, please re-authorize</span>
                  )}
                  .{" "}
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault()
                      if (canEdit) revoke.mutate()
                    }}
                  >
                    Revoke
                  </a>
                </p>
              ) : (
                <p>Authorize structure access to search and price at a structure.</p>
              )}
              <button
                type="button"
                className="secondary"
                disabled={!canEdit}
                onClick={() => void startStructureAuthorize()}
              >
                {authorized
                  ? "Re-authorize structure access"
                  : "Authorize structure access"}
              </button>
              <small className="field-hint">
                Log in with a character that has docking + market access to the
                structure. The token is stored encrypted (ADR-0029).
              </small>
            </article>

            {authorized && (
              <label>
                Structure
                <input
                  type="search"
                  value={structure ? structure.label : structureQuery}
                  placeholder="Search structures by name…"
                  aria-label="Search structure"
                  disabled={!canEdit}
                  onChange={(e) => {
                    setStructure(null)
                    setStructureQuery(e.target.value)
                  }}
                />
                {!structure && sQuery.length >= 3 && (
                  <ul className="search-results">
                    {structureResults.isLoading && (
                      <li aria-busy="true">Searching…</li>
                    )}
                    {structureResults.data?.map((s) => (
                      <li key={s.structure_id}>
                        <a
                          href="#"
                          onClick={(e) => {
                            e.preventDefault()
                            setStructure({ id: s.structure_id, label: s.name })
                            setStructureQuery("")
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

        <small className="field-hint">
          Currently pricing at{" "}
          <strong>
            {config.data.market_hub_name ?? hubName(config.data.market_hub_id)}
          </strong>{" "}
          ({isFuzzworkHub(config.data.market_hub_id) ? "Fuzzwork" : "EVE ESI"}).
        </small>

        <label>
          Default basis
          <select
            value={basis}
            disabled={!canEdit}
            onChange={(e) => setBasis(e.target.value as Basis)}
          >
            {BASES.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>

        <label>
          Default percentage
          <input
            type="number"
            min={0}
            step="0.01"
            value={percentage}
            disabled={!canEdit}
            onChange={(e) => setPercentage(e.target.value)}
            aria-label="Default percentage"
          />
        </label>

        <label>
          Price aggregate
          <select
            value={aggregate}
            disabled={!canEdit}
            onChange={(e) => setAggregate(e.target.value as AggregateField)}
          >
            {AGGREGATES.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>

        <label>
          <input
            type="checkbox"
            checked={defaultAccepted}
            disabled={!canEdit}
            onChange={(e) => setDefaultAccepted(e.target.checked)}
          />
          Accept items by default
        </label>
        <small className="field-hint">
          Off → buy <em>nothing</em> unless a pricing rule accepts it (whitelist).
        </small>

        {canEdit ? (
          <button type="submit" aria-busy={save.isPending} disabled={hubInvalid}>
            Save config
          </button>
        ) : (
          <p>
            <small>Only a Buyback Manager can change these.</small>
          </p>
        )}
        {save.isError && (
          <p className="error">{(save.error as Error).message}</p>
        )}
        {save.isSuccess && <p>Saved.</p>}
      </form>
    </>
  )
}
