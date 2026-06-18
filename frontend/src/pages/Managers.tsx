import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "react-router-dom"

import { getMe } from "../api/auth"
import { addManager, listManagers, removeManager } from "../api/managers"
import { getRosterStatus, refreshRoster, searchMembers } from "../api/roster"
import { getStructureStatus } from "../api/structures"
import { ConfirmButton } from "../components/ConfirmButton"
import { canManageCorp } from "../lib/roles"
import { refreshCooldownRemaining, relativeTime } from "../lib/roster"

export default function Managers() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const canManage = canManageCorp(me.data)

  const managers = useQuery({
    queryKey: ["managers"],
    queryFn: listManagers,
    enabled: canManage,
  })
  const roster = useQuery({
    queryKey: ["rosterStatus"],
    queryFn: getRosterStatus,
    enabled: canManage,
  })
  const structure = useQuery({
    queryKey: ["structureStatus"],
    queryFn: getStructureStatus,
    enabled: canManage,
  })
  const connected = !!structure.data?.authorized

  const [query, setQuery] = useState("")
  const q = query.trim()
  const results = useQuery({
    queryKey: ["members", q],
    queryFn: () => searchMembers(q),
    enabled: canManage && connected && q.length >= 2,
  })

  const grant = useMutation({
    mutationFn: (characterId: number) => addManager(characterId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["managers"] })
      setQuery("")
    },
  })
  const revoke = useMutation({
    mutationFn: (characterId: number) => removeManager(characterId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["managers"] }),
  })
  const refresh = useMutation({
    mutationFn: refreshRoster,
    onSuccess: (data) => queryClient.setQueryData(["rosterStatus"], data),
  })

  const cooldownMs = refreshCooldownRemaining(roster.data?.synced_at)
  const cooldownMin = Math.ceil(cooldownMs / 60000)
  const canRefresh = connected && cooldownMs === 0 && !refresh.isPending

  if (!canManage) {
    return (
      <p className="error">
        Only a CEO or Director can designate Buyback Managers.
      </p>
    )
  }

  // Already-managers don't need granting again; hide them from the picker.
  const managerIds = new Set(managers.data?.map((m) => m.character_id))

  return (
    <>
      <hgroup>
        <h1>Buyback Managers</h1>
        <p>
          Designate corp members who can edit the buyback config and pricing rules.
        </p>
      </hgroup>

      <p>
        <small className="field-hint">
          {connected ? (
            roster.data?.synced ? (
              `Roster synced ${relativeTime(roster.data.synced_at)} · ${
                roster.data.member_count
              } member${roster.data.member_count === 1 ? "" : "s"}`
            ) : (
              "Roster not synced yet — refresh to pull your corp's members."
            )
          ) : (
            <>
              Connect corp ESI access on the <Link to="/config">Config page</Link>{" "}
              to search your corp roster.
            </>
          )}
          {connected && (
            <>
              {" — "}
              <button
                type="button"
                className="linkbtn"
                disabled={!canRefresh}
                onClick={() => refresh.mutate()}
              >
                {refresh.isPending
                  ? "Refreshing…"
                  : cooldownMs > 0
                    ? `Refresh (available in ${cooldownMin} min)`
                    : "Refresh roster"}
              </button>
            </>
          )}
        </small>
      </p>
      {refresh.isError && (
        <p className="error">{(refresh.error as Error).message}</p>
      )}

      <label>
        Add a manager
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search corp members by name…"
          aria-label="Search corp members"
          disabled={!connected}
        />
        {/* Inside the label so the results panel attaches to the input (index.css
            `label:has(> .search-results) > input`). */}
        {connected && q.length >= 2 && (
          <ul className="search-results">
            {results.isLoading && <li aria-busy="true">Searching…</li>}
            {results.data
              ?.filter((m) => !managerIds.has(m.character_id))
              .map((m) => (
                <li key={m.character_id}>
                  <button
                    type="button"
                    onClick={() => grant.mutate(m.character_id)}
                  >
                    {m.name}
                  </button>
                </li>
              ))}
            {results.data?.length === 0 && (
              <li>
                No matches
                {roster.data?.synced ? "" : " — try refreshing the roster"}.
              </li>
            )}
          </ul>
        )}
      </label>
      {grant.isError && <p className="error">{(grant.error as Error).message}</p>}

      {managers.data && managers.data.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th>Manager</th>
              <th>Granted</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {managers.data.map((m) => (
              <tr key={m.character_id}>
                <td>{m.character_name}</td>
                <td>{new Date(m.granted_at).toLocaleDateString()}</td>
                <td>
                  <ConfirmButton
                    className="linkbtn"
                    label="Remove"
                    confirmPrompt="Remove manager?"
                    onConfirm={() => revoke.mutate(m.character_id)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>
          <small className="field-hint">No managers designated yet.</small>
        </p>
      )}
      {revoke.isError && (
        <p className="error">{(revoke.error as Error).message}</p>
      )}
    </>
  )
}
