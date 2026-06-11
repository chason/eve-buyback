import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"

import { getMe } from "../api/auth"
import { getConfig, updateConfig } from "../api/pricing"
import {
  beginStructureAuthorize,
  getStructureStatus,
  revokeStructure,
} from "../api/structures"
import type { AggregateField, Basis } from "../api/types"
import HubPicker, { type HubSelection } from "../components/HubPicker"
import { hubName, isFuzzworkHub } from "../lib/hubs"
import { isManager } from "../lib/roles"

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
  // The hub resolved by the HubPicker; "incomplete" blocks saving.
  const [selection, setSelection] = useState<HubSelection>({
    state: "incomplete",
  })
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

  // Seed the form once the config loads (and after a save refetch). The HubPicker
  // seeds itself from `initial` (keyed below); this mirrors the saved hub into
  // `selection` — except in the just-authorized case, where the picker opens on
  // "Player structure" with nothing picked yet.
  //
  // Guarded by content: a background refetch (e.g. on window refocus) returns a new
  // object with the same data, and re-seeding then would clobber in-progress edits —
  // and desync `selection` from the picker, which only remounts when the saved hub
  // actually changes. Only genuinely new saved data re-seeds.
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
    if (justAuthorized.current && config.data.market_hub_kind !== "structure") {
      setSelection({ state: "incomplete" })
    } else {
      setSelection({
        state: "hub",
        hubId: config.data.market_hub_id,
        kind: config.data.market_hub_kind,
        name: config.data.market_hub_name ?? null,
      })
    }
  }, [config.data])

  // Structure auth status for the authorize/revoke panel (HubPicker shares the
  // same query key for availability, so this is deduped).
  const structureStatus = useQuery({
    queryKey: ["structureStatus"],
    queryFn: getStructureStatus,
    enabled: canEdit,
  })
  const authorized = !!structureStatus.data?.authorized

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

  const structureSlot = (
    <>
      {replacedCharacter.current && (
        <p role="alert">
          ⚠️ Structure access was switched from{" "}
          <strong>{replacedCharacter.current}</strong> to the character you just
          authorized — the new token replaced the old one. If that wasn&apos;t
          intended, re-authorize as <strong>{replacedCharacter.current}</strong>.
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
              <p>
                Authorize structure access to search and price at a structure.
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
              Log in with a character that has docking + market access to the
              structure. The token is stored encrypted (ADR-0029).
            </small>
          </>
        )}
      </article>
    </>
  )

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
        <HubPicker
          key={`${config.data.market_hub_id}:${config.data.market_hub_kind}`}
          initial={{
            hubId: config.data.market_hub_id,
            kind: config.data.market_hub_kind,
            name: config.data.market_hub_name ?? null,
          }}
          forceStructureChoice={
            justAuthorized.current &&
            config.data.market_hub_kind !== "structure"
          }
          disabled={!canEdit}
          onChange={(s) => {
            justAuthorized.current = false
            setSelection(s)
          }}
          structureSlot={structureSlot}
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
