import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"

import { getMe } from "../api/auth"
import { getConfig, updateConfig } from "../api/pricing"
import { searchStations } from "../api/sde"
import {
  beginStructureAuthorize,
  getStructureStatus,
  revokeStructure,
  searchStructures,
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
  const [authorizing, setAuthorizing] = useState(false)

  // Did we just return from the structure-access SSO round-trip? (Callback
  // navigates here with ?authorized=structure.) Captured once so a later config
  // refetch or refresh doesn't re-force the structure hub. A manual hub change
  // clears it.
  const [searchParams, setSearchParams] = useSearchParams()
  const justAuthorized = useRef(searchParams.get("authorized") === "structure")
  // If the re-auth picker switched the authorizing character, Callback passes the
  // previous name here so we can warn (the swap is allowed, but worth flagging).
  const replacedCharacter = useRef(searchParams.get("replaced"))

  // Strip the one-shot param(s) from the URL after capturing them above.
  useEffect(() => {
    if (searchParams.get("authorized")) setSearchParams({}, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
      } else if (justAuthorized.current) {
        // Just authorized but no structure saved yet — keep the picker on
        // "Player structure" so the search box is ready instead of snapping
        // back to the saved (Fuzzwork) hub.
        setHubChoice(STRUCTURE)
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
  // Fetched for any manager (not just when STRUCTURE is selected) so the picker can
  // disable the structure option when the server has no token-encryption key.
  const structureStatus = useQuery({
    queryKey: ["structureStatus"],
    queryFn: getStructureStatus,
    enabled: canEdit,
  })
  const structuresAvailable = structureStatus.data?.configured !== false
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
        // Structures have no SDE to resolve against, so persist the friendly name
        // from the search (NPC/Fuzzwork names are resolved server-side).
        market_hub_name:
          hubChoice === STRUCTURE ? (structure?.label ?? null) : null,
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
    setAuthorizing(true)
    try {
      const { authorization_url } = await beginStructureAuthorize()
      // The callback routes back to the structure completion by the OAuth state
      // prefix (set server-side), so no client-side flag is needed here.
      // Full-page redirect to EVE SSO; `authorizing` stays true until unload so
      // the button keeps spinning right up to the navigation.
      window.location.href = authorization_url
    } catch {
      setAuthorizing(false)
    }
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
            onChange={(e) => {
              justAuthorized.current = false
              setHubChoice(e.target.value)
            }}
            aria-label="Market hub"
          >
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

        {hubChoice === STRUCTURE && !structuresAvailable && (
          <p className="error">
            Player-structure pricing isn&apos;t available on this server — the
            operator hasn&apos;t configured the token-encryption key
            (BUYBACK_TOKEN_ENCRYPTION_KEY). Pick another hub.
          </p>
        )}

        {hubChoice === STRUCTURE && structuresAvailable && (
          <>
            {replacedCharacter.current && (
              <p role="alert">
                ⚠️ Structure access was switched from{" "}
                <strong>{replacedCharacter.current}</strong> to the character you
                just authorized — the new token replaced the old one. If that
                wasn&apos;t intended, re-authorize as{" "}
                <strong>{replacedCharacter.current}</strong>.
              </p>
            )}
            <article>
              {structureStatus.isLoading || authorizing ? (
                <p aria-busy="true">
                  {authorizing
                    ? "Redirecting to EVE for authorization…"
                    : "Checking structure access…"}
                </p>
              ) : (
                <>
                  {authorized ? (
                    <p>
                      Structure access: connected as{" "}
                      <strong>{structureStatus.data?.character_name}</strong>
                      {structureStatus.data?.expired && (
                        <span className="error">
                          {" "}
                          — expired, please re-authorize
                        </span>
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
                    <p>
                      Authorize structure access to search and price at a
                      structure.
                    </p>
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
                    Log in with a character that has docking + market access to
                    the structure. The token is stored encrypted (ADR-0029).
                  </small>
                </>
              )}
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
