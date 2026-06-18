import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { getRosterStatus, refreshRoster } from "../api/roster"
import {
  beginStructureAuthorize,
  getStructureStatus,
  revokeStructure,
} from "../api/structures"
import { refreshCooldownRemaining, relativeTime } from "../lib/roster"

interface Props {
  /** CEO/Director: may connect, revoke, and refresh the roster. Others see it
   * read-only (the server enforces the gate regardless). */
  canManage: boolean
  /** Set when a re-authorization swapped the authorizing character — worth a warning. */
  replacedCharacterName?: string | null
}

/** The corp's single "Corp ESI access" credential (ADR-0029, ADR-0036): one EVE
 * token a CEO/Director connects to read structure markets AND the corp roster used to
 * designate Buyback Managers. Lives on the Config page. */
export default function CorpEsiAccessPanel({
  canManage,
  replacedCharacterName,
}: Props) {
  const queryClient = useQueryClient()
  const [connecting, setConnecting] = useState(false)

  const status = useQuery({
    queryKey: ["structureStatus"],
    queryFn: getStructureStatus,
  })
  const connected = !!status.data?.authorized
  const configured = status.data?.configured !== false
  const expired = !!status.data?.expired

  // The roster endpoints are CEO/Director-only — don't query them for plain managers.
  const roster = useQuery({
    queryKey: ["rosterStatus"],
    queryFn: getRosterStatus,
    enabled: canManage,
  })

  const revoke = useMutation({
    mutationFn: revokeStructure,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["structureStatus"] })
      void queryClient.invalidateQueries({ queryKey: ["rosterStatus"] })
    },
  })

  const refresh = useMutation({
    mutationFn: refreshRoster,
    onSuccess: (data) => queryClient.setQueryData(["rosterStatus"], data),
  })

  async function connect() {
    setConnecting(true)
    try {
      const { authorization_url } = await beginStructureAuthorize()
      // Full-page redirect to EVE SSO; the callback routes back by the OAuth state
      // prefix (set server-side). `connecting` stays true until navigation.
      window.location.href = authorization_url
    } catch {
      setConnecting(false)
    }
  }

  const cooldownMs = refreshCooldownRemaining(roster.data?.synced_at)
  const cooldownMin = Math.ceil(cooldownMs / 60000)
  const canRefresh = canManage && connected && cooldownMs === 0 && !refresh.isPending

  return (
    <section>
      <h2>Corp ESI access</h2>
      {replacedCharacterName && (
        <p role="alert">
          ⚠️ Corp ESI access was switched from{" "}
          <strong>{replacedCharacterName}</strong> to the character you just
          authorized — the new token replaced the old one. If that wasn&apos;t
          intended, reconnect as <strong>{replacedCharacterName}</strong>.
        </p>
      )}
      <article>
        {status.isLoading || connecting ? (
          <p aria-busy="true">
            {connecting ? "Redirecting to EVE…" : "Checking corp ESI access…"}
          </p>
        ) : !configured ? (
          <p>
            <span className="status status--offline">Unavailable</span> Corp ESI
            access isn&apos;t available on this server — the operator hasn&apos;t set
            a token-encryption key (BUYBACK_TOKEN_ENCRYPTION_KEY).
          </p>
        ) : (
          <>
            {connected && expired && (
              <p role="alert" className="error">
                ⚠️ Corp ESI access is failing
                {status.data?.failed_since
                  ? ` (since ${new Date(
                      status.data.failed_since,
                    ).toLocaleString()})`
                  : ""}
                . Structure prices and the roster may be stale until you reconnect
                with a character that still has access.
              </p>
            )}

            {connected ? (
              <p>
                <span
                  className={`status ${
                    expired ? "status--expired" : "status--online"
                  }`}
                >
                  {expired ? "Expired" : "Online"}
                </span>{" "}
                Connected as <strong>{status.data?.character_name}</strong>
                {canManage && (
                  <>
                    {" — "}
                    <button
                      type="button"
                      className="linkbtn"
                      onClick={() => revoke.mutate()}
                    >
                      Revoke
                    </button>
                  </>
                )}
              </p>
            ) : (
              <p>
                <span className="status status--offline">Offline</span> Not
                connected. Connect one EVE token to price at player structures and
                to search your roster when designating managers.
              </p>
            )}

            <button
              type="button"
              className="secondary"
              disabled={!canManage}
              onClick={() => void connect()}
            >
              {connected ? "Reconnect corp ESI access" : "Connect corp ESI access"}
            </button>
            <small className="field-hint">
              {canManage
                ? "Authorize as a character that can dock at your structure(s) and read your corp's member list. The refresh token is stored encrypted; we use it only to read structure markets and the roster."
                : "Only a CEO or Director can connect corp ESI access."}
            </small>

            {canManage && connected && (
              <p>
                <small className="field-hint">
                  Roster:{" "}
                  {roster.data?.synced
                    ? `synced ${relativeTime(roster.data.synced_at)} · ${
                        roster.data.member_count
                      } member${roster.data.member_count === 1 ? "" : "s"}`
                    : "not synced yet"}
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
                </small>
                {refresh.isError && (
                  <>
                    {" "}
                    <small className="error">
                      {(refresh.error as Error).message}
                    </small>
                  </>
                )}
              </p>
            )}
          </>
        )}
      </article>
    </section>
  )
}
