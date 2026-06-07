import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import { getMe } from "../api/auth"
import { deleteRule, listRules, putRule } from "../api/pricing"
import { listMarketGroups, searchTypes } from "../api/sde"
import type { Basis, RuleOut, TargetKind } from "../api/types"
import { isManager } from "../lib/roles"

const BASES: Basis[] = ["buy", "sell", "split"]

export default function Rules() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const rules = useQuery({ queryKey: ["rules"], queryFn: listRules })
  const groups = useQuery({
    queryKey: ["market-groups"],
    queryFn: listMarketGroups,
  })
  const canEdit = isManager(me.data?.role)

  const groupName = useMemo(() => {
    const byId = new Map((groups.data ?? []).map((g) => [g.market_group_id, g.name]))
    return (id: number) => byId.get(id) ?? `Market group ${id}`
  }, [groups.data])

  function targetLabel(rule: RuleOut): string {
    // The backend resolves the SDE name; fall back to a group lookup / the raw id
    // only if it's missing (e.g. the target was removed from the SDE).
    if (rule.target_name) return rule.target_name
    return rule.target_kind === "market_group"
      ? groupName(rule.target_id)
      : `Type ${rule.target_id}`
  }

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["rules"] })

  const remove = useMutation({
    mutationFn: (rule: RuleOut) => deleteRule(rule.target_kind, rule.target_id),
    onSuccess: invalidate,
  })

  if (rules.isLoading) return <p aria-busy="true">Loading…</p>
  if (rules.isError || !rules.data) {
    return <p className="error">Could not load pricing rules.</p>
  }

  return (
    <>
      <hgroup>
        <h1>Pricing rules</h1>
        <p>Per-type and per-market-group overrides on the corp defaults.</p>
      </hgroup>

      {rules.data.length === 0 ? (
        <p>No rules yet — the corp defaults apply to everything.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Target</th>
              <th>Basis</th>
              <th>%</th>
              <th>Enabled</th>
              {canEdit && <th />}
            </tr>
          </thead>
          <tbody>
            {rules.data.map((rule) => (
              <tr key={`${rule.target_kind}-${rule.target_id}`}>
                <td>{targetLabel(rule)}</td>
                <td>{rule.basis ?? "(default)"}</td>
                <td className="num">{rule.percentage}</td>
                <td>{rule.enabled ? "yes" : "no"}</td>
                {canEdit && (
                  <td>
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault()
                        remove.mutate(rule)
                      }}
                    >
                      Remove
                    </a>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canEdit ? (
        <AddRule onSaved={invalidate} groupOptions={groups.data ?? []} />
      ) : (
        <p>
          <small>Only a Buyback Manager can change pricing rules.</small>
        </p>
      )}
    </>
  )
}

function AddRule({
  onSaved,
  groupOptions,
}: {
  onSaved: () => void
  groupOptions: { market_group_id: number; name: string }[]
}) {
  const [kind, setKind] = useState<TargetKind>("type")
  const [target, setTarget] = useState<{ id: number; name: string } | null>(null)
  const [search, setSearch] = useState("")
  const [basis, setBasis] = useState<Basis | "">("")
  const [percentage, setPercentage] = useState("90")
  const [enabled, setEnabled] = useState(true)

  const query = search.trim()
  const results = useQuery({
    queryKey: ["types", query],
    queryFn: () => searchTypes(query),
    enabled: kind === "type" && query.length >= 2,
  })

  const save = useMutation({
    mutationFn: () =>
      putRule(kind, target!.id, {
        basis: basis === "" ? null : basis,
        percentage,
        enabled,
      }),
    onSuccess: () => {
      setTarget(null)
      setSearch("")
      onSaved()
    },
  })

  return (
    <article>
      <header>Add or replace a rule</header>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (target) save.mutate()
        }}
      >
        <fieldset>
          <label>
            Target kind
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as TargetKind)
                setTarget(null)
                setSearch("")
              }}
            >
              <option value="type">Type</option>
              <option value="market_group">Market group</option>
            </select>
          </label>
        </fieldset>

        {kind === "type" ? (
          <label>
            Type
            <input
              type="search"
              value={target ? target.name : search}
              placeholder="Search by name…"
              aria-label="Search type by name"
              onChange={(e) => {
                setTarget(null)
                setSearch(e.target.value)
              }}
            />
            {!target && query.length >= 2 && (
              <ul className="search-results">
                {results.isLoading && <li aria-busy="true">Searching…</li>}
                {results.data?.map((t) => (
                  <li key={t.type_id}>
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault()
                        setTarget({ id: t.type_id, name: t.name })
                      }}
                    >
                      {t.name}
                    </a>
                  </li>
                ))}
                {results.data?.length === 0 && <li>No matches.</li>}
              </ul>
            )}
          </label>
        ) : (
          <label>
            Market group
            <select
              value={target?.id ?? ""}
              onChange={(e) => {
                const id = Number(e.target.value)
                const g = groupOptions.find((x) => x.market_group_id === id)
                setTarget(g ? { id, name: g.name } : null)
              }}
            >
              <option value="">Select a market group…</option>
              {groupOptions.map((g) => (
                <option key={g.market_group_id} value={g.market_group_id}>
                  {g.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="grid">
          <label>
            Basis
            <select
              value={basis}
              onChange={(e) => setBasis(e.target.value as Basis | "")}
            >
              <option value="">(default)</option>
              {BASES.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </label>
          <label>
            Percentage
            <input
              type="number"
              min={0}
              step="0.01"
              value={percentage}
              aria-label="Rule percentage"
              onChange={(e) => setPercentage(e.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            Enabled
          </label>
        </div>

        <button type="submit" disabled={!target} aria-busy={save.isPending}>
          Save rule
        </button>
        {save.isError && (
          <p className="error">{(save.error as Error).message}</p>
        )}
      </form>
    </article>
  )
}
