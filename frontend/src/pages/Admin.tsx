import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import type { CorpAccessOut } from "../api/admin"
import { grantCorpAccess, listCorpAccess, revokeCorpAccess } from "../api/admin"
import { getMe } from "../api/auth"
import { ConfirmButton } from "../components/ConfirmButton"

/** What the access cell says, in plain English (no billing/entitlement jargon). */
function accessLabel(corp: CorpAccessOut): string {
  if (corp.active) return "On"
  if (corp.granted_at) return "Expired"
  return "Off"
}

function untilLabel(corp: CorpAccessOut): string {
  if (!corp.granted_at) return "—"
  if (!corp.expires_at) return "Forever"
  // EVE runs on UTC; render the expiry in UTC so it matches the date the admin picked.
  return new Date(corp.expires_at).toLocaleDateString(undefined, {
    timeZone: "UTC",
  })
}

function sourceLabel(corp: CorpAccessOut): string | null {
  if (!corp.granted_at) return null
  return corp.source === "payment" ? "paid" : "granted by admin"
}

function AccessRow({ corp }: { corp: CorpAccessOut }) {
  const queryClient = useQueryClient()
  // Optional "until" date for the grant; empty = access never expires.
  const [until, setUntil] = useState("")

  const grant = useMutation({
    mutationFn: () =>
      grantCorpAccess(
        corp.corporation_id,
        // A picked date means "through that day" (end of day, EVE/UTC time).
        until ? `${until}T23:59:59Z` : null,
      ),
    onSuccess: () => {
      setUntil("")
      void queryClient.invalidateQueries({ queryKey: ["corpAccess"] })
    },
  })
  const revoke = useMutation({
    mutationFn: () => revokeCorpAccess(corp.corporation_id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["corpAccess"] }),
  })

  const source = sourceLabel(corp)
  return (
    <tr>
      <td>{corp.corporation_name}</td>
      <td>
        {corp.active ? <mark>On</mark> : accessLabel(corp)}
        {source && (
          <>
            {" "}
            <small className="field-hint">({source})</small>
          </>
        )}
      </td>
      <td>{untilLabel(corp)}</td>
      <td className="access-actions">
        <input
          type="date"
          value={until}
          onChange={(e) => setUntil(e.target.value)}
          aria-label={`Access until date for ${corp.corporation_name}`}
          title="Leave empty for access that never expires"
        />
        <button
          type="button"
          className="secondary"
          disabled={grant.isPending}
          onClick={() => grant.mutate()}
        >
          {corp.active ? "Update access" : "Give access"}
        </button>
        {corp.granted_at != null && (
          <ConfirmButton
            className="linkbtn"
            label="Remove"
            title="Remove access?"
            prompt="The corporation will lose access to the paid features."
            confirmLabel="Remove access"
            onConfirm={() => revoke.mutate()}
          />
        )}
        {grant.isError && (
          <p className="error">{(grant.error as Error).message}</p>
        )}
        {revoke.isError && (
          <p className="error">{(revoke.error as Error).message}</p>
        )}
      </td>
    </tr>
  )
}

export default function Admin() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })
  const isAdmin = !!me.data?.is_app_admin

  const access = useQuery({
    queryKey: ["corpAccess"],
    queryFn: listCorpAccess,
    enabled: isAdmin,
  })

  if (me.data && !isAdmin) {
    return <p className="error">Only an app admin can manage access.</p>
  }

  return (
    <>
      <hgroup>
        <h1>Admin</h1>
        <p>
          Give corporations access to the paid features, or take it away. Access
          with no date lasts forever; paid access renews when a payment arrives.
        </p>
      </hgroup>

      {access.isLoading && <p aria-busy="true">Loading corporations…</p>}
      {access.isError && (
        <p className="error">{(access.error as Error).message}</p>
      )}

      {access.data &&
        (access.data.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Corporation</th>
                <th>Access</th>
                <th>Until</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {access.data.map((corp) => (
                <AccessRow key={corp.corporation_id} corp={corp} />
              ))}
            </tbody>
          </table>
        ) : (
          <p>
            <small className="field-hint">No corporations registered yet.</small>
          </p>
        ))}
    </>
  )
}
