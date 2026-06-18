import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"

import { getMe } from "../api/auth"
import { getConfig, updateConfig } from "../api/pricing"
import type { AggregateField, Basis } from "../api/types"
import CorpEsiAccessPanel from "../components/CorpEsiAccessPanel"
import HubPicker, { type HubSelection } from "../components/HubPicker"
import { hubName, isFuzzworkHub } from "../lib/hubs"
import { canManageCorp, isManager } from "../lib/roles"

const BASES: Basis[] = ["buy", "sell", "split"]
const BASIS_LABELS: Record<Basis, string> = {
  buy: "Buy orders",
  sell: "Sell orders",
  split: "Split (buy/sell midpoint)",
}

const AGGREGATES: AggregateField[] = [
  "percentile",
  "weighted_average",
  "median",
  "max",
  "min",
]
const AGGREGATE_LABELS: Record<AggregateField, string> = {
  percentile: "Percentile",
  weighted_average: "Weighted average",
  median: "Median",
  max: "Max",
  min: "Min",
}

export default function Config() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig })
  const canEdit = isManager(me.data?.role)

  const [basis, setBasis] = useState<Basis>("buy")
  const [percentage, setPercentage] = useState("90")
  const [aggregate, setAggregate] = useState<AggregateField>("percentile")
  const [defaultAccepted, setDefaultAccepted] = useState(true)
  // The hub resolved by the HubPicker; "incomplete" blocks saving.
  const [selection, setSelection] = useState<HubSelection>({
    state: "incomplete",
  })

  // If a re-auth picker switched the authorizing character, Callback passes the
  // previous name here so the Corp ESI access panel can warn about the swap.
  const [searchParams, setSearchParams] = useSearchParams()
  const replacedCharacter = useRef(searchParams.get("replaced"))

  // Strip the one-shot param(s) from the URL after capturing them above.
  useEffect(() => {
    if (searchParams.get("authorized") || searchParams.get("replaced")) {
      setSearchParams({}, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Seed the form once the config loads (and after a save refetch). The HubPicker
  // seeds itself from `initial` (keyed below); this mirrors the saved hub into
  // `selection`. Guarded by content so a background refetch (e.g. window refocus)
  // doesn't clobber in-progress edits / desync `selection` from the picker.
  const seededConfig = useRef<string | null>(null)
  useEffect(() => {
    if (!config.data) return
    const signature = JSON.stringify(config.data)
    if (seededConfig.current === signature) return
    seededConfig.current = signature

    setBasis(config.data.default_basis)
    setPercentage(config.data.default_percentage)
    setAggregate(config.data.aggregate_field)
    setDefaultAccepted(config.data.default_accepted)
    setSelection({
      state: "hub",
      hubId: config.data.market_hub_id,
      kind: config.data.market_hub_kind,
      name: config.data.market_hub_name ?? null,
    })
  }, [config.data])

  const save = useMutation({
    mutationFn: () =>
      updateConfig({
        market_hub_id: selection.state === "hub" ? selection.hubId : "",
        market_hub_kind:
          selection.state === "hub" ? selection.kind : "npc_station",
        // Structures have no SDE to resolve against, so persist the friendly name
        // from the search (NPC/Fuzzwork names are resolved server-side).
        market_hub_name:
          selection.state === "hub" && selection.kind === "structure"
            ? selection.name
            : null,
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

      <CorpEsiAccessPanel
        canManage={canManageCorp(me.data)}
        replacedCharacterName={replacedCharacter.current}
      />

      <form
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <HubPicker
          key={`${config.data.market_hub_id}:${config.data.market_hub_kind}`}
          initial={{
            hubId: config.data.market_hub_id,
            kind: config.data.market_hub_kind,
            name: config.data.market_hub_name ?? null,
          }}
          disabled={!canEdit}
          onChange={setSelection}
        />

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
                {BASIS_LABELS[b]}
              </option>
            ))}
          </select>
        </label>
        <small className="field-hint">
          Which side of the market to price from — what buyers bid (Buy), what
          sellers ask (Sell), or the midpoint between them (Split). Buy is the usual
          choice for a buyback.
        </small>

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
        <small className="field-hint">
          Share of the market price members are paid — e.g. <strong>90</strong> pays
          90% of the basis price.
        </small>

        <label>
          Price aggregate
          <select
            value={aggregate}
            disabled={!canEdit}
            onChange={(e) => setAggregate(e.target.value as AggregateField)}
          >
            {AGGREGATES.map((a) => (
              <option key={a} value={a}>
                {AGGREGATE_LABELS[a]}
              </option>
            ))}
          </select>
        </label>
        <small className="field-hint">
          How to summarise the order book into one price.{" "}
          <strong>Percentile</strong> ignores a few outlier orders, so a single
          manipulated order can&apos;t skew the quote — the recommended default.
        </small>

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
          <button
            type="submit"
            aria-busy={save.isPending}
            disabled={selection.state !== "hub"}
          >
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
