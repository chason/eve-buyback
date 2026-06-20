import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import { getMe } from "../api/auth"
import { deleteRule, listRules, putRule } from "../api/pricing"
import { listMarketGroups, searchTypes } from "../api/sde"
import type { Basis, RuleOut, TargetKind } from "../api/types"
import { ConfirmButton } from "../components/ConfirmButton"
import HubPicker, { type HubSelection } from "../components/HubPicker"
import { StatusChip } from "../components/StatusChip"
import { hubName } from "../lib/hubs"
import { isManager } from "../lib/roles"

const BASES: Basis[] = ["buy", "sell", "split"]

// The bucket for rules with no custom folder, in the "My folders" view (ADR-0039).
const UNGROUPED = "Ungrouped"

// Reprocess pricing only applies to ores (ADR-0026): the three ore branches under
// "Raw Materials". A target is eligible if its market-group path passes through one.
const ORE_BRANCHES = new Set(["Standard Ores", "Moon Ores", "Ice Ores"])

export default function Rules() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const rules = useQuery({ queryKey: ["rules"], queryFn: listRules })
  const groups = useQuery({
    queryKey: ["market-groups"],
    queryFn: listMarketGroups,
  })
  const canEdit = isManager(me.data?.role)
  // "Group by" view (ADR-0039) + the rule being edited (pre-loaded into the form below).
  const [groupBy, setGroupBy] = useState<"category" | "folders">("category")
  const [editing, setEditing] = useState<RuleOut | null>(null)

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

  // Market-group ids that sit in (or under) an ore branch — reprocess-eligible.
  const oreGroupIds = useMemo(() => {
    const s = new Set<number>()
    for (const g of groupOptions) {
      if (g.path.split(" / ").some((seg) => ORE_BRANCHES.has(seg))) s.add(g.id)
    }
    return s
  }, [groupOptions])

  // The top-level market-group name a rule files under — its category folder. Walks the
  // market tree up to the root; rules whose target has no/unknown group go in "Other".
  const topLevelGroup = useMemo(() => {
    const byId = new Map((groups.data ?? []).map((g) => [g.market_group_id, g]))
    return (mgId: number | null | undefined): string => {
      if (mgId == null) return "Other"
      let cur = byId.get(mgId)
      const seen = new Set<number>()
      while (
        cur?.parent_id != null &&
        byId.has(cur.parent_id) &&
        !seen.has(cur.market_group_id)
      ) {
        seen.add(cur.market_group_id)
        cur = byId.get(cur.parent_id)
      }
      return cur?.name ?? "Other"
    }
  }, [groups.data])

  // Rules grouped into category folders, sorted by folder name ("Other" last).
  const folders = useMemo(() => {
    const map = new Map<string, RuleOut[]>()
    for (const rule of rules.data ?? []) {
      const key = topLevelGroup(rule.target_market_group_id)
      const list = map.get(key) ?? []
      list.push(rule)
      map.set(key, list)
    }
    return [...map.entries()].sort(([a], [b]) =>
      a === "Other" ? 1 : b === "Other" ? -1 : a.localeCompare(b),
    )
  }, [rules.data, topLevelGroup])

  // Distinct custom-folder names (for the editor's combobox), sorted.
  const existingFolders = useMemo(
    () =>
      [
        ...new Set(
          (rules.data ?? [])
            .map((r) => r.folder?.trim())
            .filter((f): f is string => !!f),
        ),
      ].sort((a, b) => a.localeCompare(b)),
    [rules.data],
  )

  // Rules grouped by their custom folder (ADR-0039); unfiled rules go in "Ungrouped",
  // sorted last. Folder names sort alphabetically.
  const customFolders = useMemo(() => {
    const map = new Map<string, RuleOut[]>()
    for (const rule of rules.data ?? []) {
      const key = rule.folder?.trim() || UNGROUPED
      const list = map.get(key) ?? []
      list.push(rule)
      map.set(key, list)
    }
    return [...map.entries()].sort(([a], [b]) =>
      a === UNGROUPED ? 1 : b === UNGROUPED ? -1 : a.localeCompare(b),
    )
  }, [rules.data])

  const displayFolders = groupBy === "folders" ? customFolders : folders

  function startEdit(rule: RuleOut) {
    setEditing(rule)
    requestAnimationFrame(() =>
      document
        .getElementById("rule-form")
        ?.scrollIntoView?.({ behavior: "smooth", block: "center" }),
    )
  }

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

  const renderRow = (rule: RuleOut) => (
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
      <td>
        {rule.accepted ? (
          <StatusChip variant="accepted">Yes</StatusChip>
        ) : (
          <StatusChip variant="danger">No</StatusChip>
        )}
      </td>
      <td>{rule.basis ?? "(default)"}</td>
      <td className="num">{rule.percentage}</td>
      <td>
        {rule.market_hub_id
          ? (rule.market_hub_name ?? hubName(rule.market_hub_id))
          : "(default)"}
      </td>
      <td>
        {rule.reprocess ? <StatusChip variant="info">Yes</StatusChip> : "–"}
      </td>
      <td>
        {rule.compressed_only ? (
          <StatusChip variant="info">Yes</StatusChip>
        ) : (
          "–"
        )}
      </td>
      <td>
        {rule.enabled ? (
          <StatusChip variant="accepted">On</StatusChip>
        ) : (
          <StatusChip variant="muted">Off</StatusChip>
        )}
      </td>
      {canEdit && (
        <td>
          <button
            type="button"
            className="linkbtn"
            onClick={() => startEdit(rule)}
          >
            Edit
          </button>
          {" · "}
          <ConfirmButton
            className="linkbtn"
            label="Remove"
            title="Remove rule?"
            prompt="This pricing rule will be deleted."
            confirmLabel="Remove rule"
            onConfirm={() => remove.mutate(rule)}
          />
        </td>
      )}
    </tr>
  )

  if (rules.isLoading) return <p aria-busy="true">Loading…</p>
  if (rules.isError || !rules.data) {
    return <p className="error">Could not load pricing rules.</p>
  }

  return (
    <>
      <div className="rules-head">
        <hgroup>
          <h1>Pricing rules</h1>
          <p>Per-type and per-market-group overrides on the corp defaults.</p>
        </hgroup>
        {rules.data.length > 0 && (
          <div className="groupby">
            <span className="groupby-label">Group by</span>
            <div className="seg" role="group" aria-label="Group rules by">
              <button
                type="button"
                className={groupBy === "category" ? "on" : ""}
                aria-pressed={groupBy === "category"}
                onClick={() => setGroupBy("category")}
              >
                Category
              </button>
              <button
                type="button"
                className={groupBy === "folders" ? "on" : ""}
                aria-pressed={groupBy === "folders"}
                onClick={() => setGroupBy("folders")}
              >
                My folders
              </button>
            </div>
          </div>
        )}
      </div>

      {rules.data.length === 0 ? (
        <p>No rules yet — the corp defaults apply to everything.</p>
      ) : (
        <div className="panel">
          {displayFolders.map(([folderName, folderRules]) => (
            <details key={folderName} className="rule-folder" open>
              <summary>
                {folderName}
                <span className="rule-folder-count">{folderRules.length}</span>
              </summary>
              <table>
                <thead>
                  <tr>
                    <th>Target</th>
                    <th>Accept</th>
                    <th>Basis</th>
                    <th>%</th>
                    <th>Hub</th>
                    <th>Reprocess</th>
                    <th>Compressed</th>
                    <th>Enabled</th>
                    {canEdit && <th />}
                  </tr>
                </thead>
                <tbody>{folderRules.map(renderRow)}</tbody>
              </table>
            </details>
          ))}
        </div>
      )}

      {canEdit ? (
        <AddRule
          key={editing ? `${editing.target_kind}-${editing.target_id}` : "new"}
          initial={editing}
          existingFolders={existingFolders}
          onSaved={() => {
            invalidate()
            setEditing(null)
          }}
          onCancel={() => setEditing(null)}
          groupOptions={groupOptions}
          oreGroupIds={oreGroupIds}
        />
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
  onCancel,
  initial,
  existingFolders,
  groupOptions,
  oreGroupIds,
}: {
  onSaved: () => void
  onCancel: () => void
  initial: RuleOut | null
  existingFolders: string[]
  groupOptions: { id: number; leaf: string; path: string }[]
  oreGroupIds: Set<number>
}) {
  // Edit mode pre-loads a rule (the component is remounted via `key`, so these
  // initializers re-run with the rule's values).
  const editing = initial !== null
  const [kind, setKind] = useState<TargetKind>(initial?.target_kind ?? "type")
  const [target, setTarget] = useState<{
    id: number
    name: string
    marketGroupId?: number | null
  } | null>(
    initial
      ? {
          id: initial.target_id,
          name:
            initial.target_name ??
            (initial.target_kind === "market_group"
              ? `Market group ${initial.target_id}`
              : `Type ${initial.target_id}`),
          marketGroupId: initial.target_market_group_id,
        }
      : null,
  )
  const [search, setSearch] = useState("")
  const [basis, setBasis] = useState<Basis | "">(initial?.basis ?? "")
  const [percentage, setPercentage] = useState(
    initial ? String(initial.percentage) : "90",
  )
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [accepted, setAccepted] = useState(initial?.accepted ?? true)
  const [reprocess, setReprocess] = useState(initial?.reprocess ?? false)
  const [compressedOnly, setCompressedOnly] = useState(
    initial?.compressed_only ?? false,
  )
  const [folder, setFolder] = useState(initial?.folder ?? "")
  // Optional per-rule hub override (ADR-0031); "default" → corp default hub. Seeded from
  // the edited rule so a re-save doesn't silently clear an existing override.
  const [hubSel, setHubSel] = useState<HubSelection>(() =>
    initial?.market_hub_id
      ? {
          state: "hub",
          hubId: initial.market_hub_id,
          kind: initial.market_hub_kind ?? "npc_station",
          name: initial.market_hub_name ?? null,
        }
      : { state: "default" },
  )

  // Reprocess only applies to ores: a market-group target in an ore branch, or a
  // type whose market group is in one (ADR-0026). Hidden/ignored otherwise.
  const reprocessEligible =
    !!target &&
    (kind === "market_group"
      ? oreGroupIds.has(target.id)
      : target.marketGroupId != null && oreGroupIds.has(target.marketGroupId))

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
        accepted,
        reprocess: reprocessEligible && reprocess,
        compressed_only: reprocessEligible && compressedOnly,
        // Folder is a free-text label; blank files the rule under its market category.
        folder: folder.trim() || null,
        // Hub override rides only when a concrete hub is picked; "(corp default)"
        // sends nothing, which clears any previous override (PUT replaces).
        ...(accepted && hubSel.state === "hub"
          ? {
              market_hub_id: hubSel.hubId,
              market_hub_kind: hubSel.kind,
              // Name only matters for structures (no SDE to resolve against).
              market_hub_name:
                hubSel.kind === "structure" ? hubSel.name : null,
            }
          : {}),
      }),
    onSuccess: () => {
      setTarget(null)
      setSearch("")
      onSaved()
    },
  })

  return (
    <article id="rule-form">
      <header>
        {editing ? `Edit rule — ${target?.name ?? ""}` : "Add or replace a rule"}
      </header>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (target) save.mutate()
        }}
      >
        {editing ? (
          <label>
            Target
            <input
              type="text"
              value={target?.name ?? ""}
              readOnly
              aria-label="Rule target"
            />
            <small className="field-hint">
              Editing this rule. To target a different item, add a new rule
              instead.
            </small>
          </label>
        ) : (
          <>
        <fieldset>
          <label>
            Target kind
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as TargetKind)
                setTarget(null)
                setSearch("")
                setReprocess(false)
                setCompressedOnly(false)
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
                    <button
                      type="button"
                      onClick={() =>
                        setTarget({
                          id: t.type_id,
                          name: t.name,
                          marketGroupId: t.market_group_id,
                        })
                      }
                    >
                      {t.name}
                    </button>
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
                    <button
                      type="button"
                      onClick={() => setTarget({ id: g.id, name: g.path })}
                    >
                      {g.path}
                    </button>
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
          </>
        )}

        <div className="rule-flags">
          <label>
            <input
              type="checkbox"
              checked={accepted}
              onChange={(e) => setAccepted(e.target.checked)}
            />
            Accept (buy this item)
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

        {accepted ? (
          <>
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
            </div>
            <HubPicker
              defaultOption="(corp default hub)"
              onChange={setHubSel}
              initial={
                initial?.market_hub_id
                  ? {
                      hubId: initial.market_hub_id,
                      kind: initial.market_hub_kind ?? "npc_station",
                      name: initial.market_hub_name ?? null,
                    }
                  : null
              }
            />
            {reprocessEligible && (
              <div className="rule-flags">
                <label>
                  <input
                    type="checkbox"
                    checked={reprocess}
                    onChange={(e) => setReprocess(e.target.checked)}
                  />
                  Reprocess (ore → minerals)
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={compressedOnly}
                    onChange={(e) => setCompressedOnly(e.target.checked)}
                  />
                  Compressed only
                </label>
              </div>
            )}
            {reprocessEligible && (
              <small className="field-hint">
                <strong>Compressed only</strong> rejects the uncompressed variants
                of matched ores — only the <em>Compressed</em> versions are bought.
              </small>
            )}
          </>
        ) : (
          <p>
            <small>This rule rejects the item — it won't be bought.</small>
          </p>
        )}

        <label>
          Folder <small className="field-hint">(optional)</small>
          <input
            type="text"
            list="rule-folders"
            value={folder}
            placeholder="Pick a folder or type a new name…"
            aria-label="Rule folder"
            onChange={(e) => setFolder(e.target.value)}
          />
          <datalist id="rule-folders">
            {existingFolders.map((f) => (
              <option key={f} value={f} />
            ))}
          </datalist>
          <small className="field-hint">
            Files the rule under <strong>My folders</strong>; blank groups it by
            the item&apos;s market category.
          </small>
        </label>

        <div className="rule-form-actions">
          <button
            type="submit"
            disabled={!target || (accepted && hubSel.state === "incomplete")}
            aria-busy={save.isPending}
          >
            {editing ? "Save changes" : "Save rule"}
          </button>
          {editing && (
            <button type="button" className="secondary" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
        {save.isError && (
          <p className="error">{(save.error as Error).message}</p>
        )}
      </form>
    </article>
  )
}
