import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { getMe } from "../api/auth"
import { getConfig, updateConfig } from "../api/pricing"
import { searchStations } from "../api/sde"
import type { AggregateField, Basis } from "../api/types"
import { FUZZWORK_HUBS, hubName, isFuzzworkHub } from "../lib/hubs"
import { isManager } from "../lib/roles"

const CUSTOM = "custom" // sentinel select value for "other NPC station"
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
  // Hub picker: a Fuzzwork preset id (as a string) or CUSTOM for any other NPC
  // station, chosen from a searchable list (priced via ESI).
  const [hubChoice, setHubChoice] = useState<string>(String(FUZZWORK_HUBS[0].id))
  const [stationQuery, setStationQuery] = useState("")
  const [station, setStation] = useState<{ id: number; label: string } | null>(
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
      if (isFuzzworkHub(hub)) {
        setHubChoice(String(hub))
      } else {
        setHubChoice(CUSTOM)
        setStation({
          id: hub,
          label: config.data.market_hub_name ?? `Station ${hub}`,
        })
      }
    }
  }, [config.data])

  const query = stationQuery.trim()
  const stationResults = useQuery({
    queryKey: ["stations", query],
    queryFn: () => searchStations(query),
    enabled: hubChoice === CUSTOM && query.length >= 2,
  })

  const hubId = hubChoice === CUSTOM ? (station?.id ?? 0) : Number(hubChoice)
  const hubInvalid = hubChoice === CUSTOM && station === null

  const save = useMutation({
    mutationFn: () =>
      updateConfig({
        market_hub_id: hubId,
        market_hub_kind: "npc_station",
        default_basis: basis,
        default_percentage: percentage,
        aggregate_field: aggregate,
        default_accepted: defaultAccepted,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["config"] }),
  })

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
              <option key={h.id} value={String(h.id)}>
                {h.name}
              </option>
            ))}
            <option value={CUSTOM}>Other NPC station…</option>
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
                          id: s.station_id,
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
