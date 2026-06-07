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

  // Full market-tree path of a group, e.g. "Manufacture & Research / Materials /
  // Raw Materials / Standard Ores / Veldspar". Market-group *names* repeat heavily
  // (19 groups are literally "ORE" — the Outer Ring Excavations ship faction, not
  // asteroid ore), so the path is what makes a group unambiguous.
  const groupPath = useMemo(() => {
    const byId = new Map((groups.data ?? []).map((g) => [g.market_group_id, g]))
    return (id: number): string => {
      const parts: string[] = []
      const seen = new Set<number>()
      let cur = byId.get(id)
      while (cur && !seen.has(cur.market_group_id)) {
        seen.add(cur.market_group_id)
        parts.unshift(cur.name)
        cur = cur.parent_id != null ? byId.get(cur.parent_id) : undefined
      }
      return parts.join(" / ") || `Market group ${id}`
    }
  }, [groups.data])

  // Picker options: each group's leaf name (what the search matches) + its full path
  // (what's shown, to disambiguate the many repeated names). Sorted by path.
  const groupOptions = useMemo(
    () =>
      (groups.data ?? [])
        .map((g) => ({
          id: g.market_group_id,
          leaf: g.name,
          path: groupPath(g.market_group_id),
        }))
        .sort((a, b) => a.path.localeCompare(b.path)),
    [groups.data, groupPath],
  )

  function targetLabel(rule: RuleOut): string {
    // The backend resolves the SDE name; fall back to a path lookup / the raw id
    // only if it's missing (e.g. the target was removed from the SDE).
    if (rule.target_name) return rule.target_name
    return rule.target_kind === "market_group"
      ? groupPath(rule.target_id)
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
              <th>Reprocess</th>
              <th>Enabled</th>
              {canEdit && <th />}
            </tr>
          </thead>
          <tbody>
            {rules.data.map((rule) => (
              <tr key={`${rule.target_kind}-${rule.target_id}`}>
                <td
                  title={
                    rule.target_kind === "market_group"
                      ? groupPath(rule.target_id)
                      : undefined
                  }
                >
                  {targetLabel(rule)}
                </td>
                <td>{rule.basis ?? "(default)"}</td>
                <td className="num">{rule.percentage}</td>
                <td>{rule.reprocess ? "yes" : "–"}</td>
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
        <AddRule onSaved={invalidate} groupOptions={groupOptions} />
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
  groupOptions: { id: number; leaf: string; path: string }[]
}) {
  const [kind, setKind] = useState<TargetKind>("type")
  const [target, setTarget] = useState<{ id: number; name: string } | null>(null)
  const [search, setSearch] = useState("")
  const [basis, setBasis] = useState<Basis | "">("")
  const [percentage, setPercentage] = useState("90")
  const [enabled, setEnabled] = useState(true)
  const [reprocess, setReprocess] = useState(false)

  const query = search.trim()
  const results = useQuery({
    queryKey: ["types", query],
    queryFn: () => searchTypes(query),
    enabled: kind === "type" && query.length >= 2,
  })

  // Market groups are already loaded; filter locally by matching the query anywhere
  // in a group's path. A match on the group's own name ranks above an ancestor-only
  // match, so searching "Raw Materials" lists that group first, then everything under
  // it. Ties break by depth (shallower first) then path. Capped for rendering.
  const groupMatches = useMemo(() => {
    if (kind !== "market_group" || query.length < 2) return []
    const q = query.toLowerCase()
    const scored: { g: (typeof groupOptions)[number]; score: number; depth: number }[] =
      []
    for (const g of groupOptions) {
      const leaf = g.leaf.toLowerCase()
      let score: number | null = null
      if (leaf === q) score = 0
      else if (leaf.includes(q)) score = 1
      else if (g.path.toLowerCase().includes(q)) score = 2
      if (score === null) continue
      scored.push({ g, score, depth: g.path.split(" / ").length })
    }
    scored.sort(
      (a, b) =>
        a.score - b.score || a.depth - b.depth || a.g.path.localeCompare(b.g.path),
    )
    return scored.slice(0, 50).map((s) => s.g)
  }, [kind, query, groupOptions])

  const save = useMutation({
    mutationFn: () =>
      putRule(kind, target!.id, {
        basis: basis === "" ? null : basis,
        percentage,
        enabled,
        reprocess,
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
            <input
              type="search"
              value={target ? target.name : search}
              placeholder="Search by name (e.g. Standard Ores)…"
              aria-label="Search market group by name"
              onChange={(e) => {
                setTarget(null)
                setSearch(e.target.value)
              }}
            />
            {!target && query.length >= 2 && (
              <ul className="search-results">
                {groupMatches.map((g) => (
                  <li key={g.id}>
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault()
                        setTarget({ id: g.id, name: g.path })
                      }}
                    >
                      {g.path}
                    </a>
                  </li>
                ))}
                {groupMatches.length === 0 && <li>No matches.</li>}
              </ul>
            )}
            <small>
              Matches anywhere in the path — search a parent (e.g.{" "}
              <em>Raw Materials</em>) to list everything under it.
            </small>
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
          <label>
            <input
              type="checkbox"
              checked={reprocess}
              onChange={(e) => setReprocess(e.target.checked)}
            />
            Reprocess (ore → minerals)
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
