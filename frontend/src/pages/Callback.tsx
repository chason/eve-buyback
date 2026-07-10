import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { completeWalletAuthorize, WALLET_STATE_PREFIX } from "../api/admin"
import { login } from "../api/auth"
import {
  completeStructureAuthorize,
  STRUCTURE_STATE_PREFIX,
} from "../api/structures"

export default function Callback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const handled = useRef(false)
  // The same SSO callback completes a login, a Corp ESI access grant, or the
  // operator-wallet grant (ADR-0042). Route on the OAuth `state` echoed back by
  // EVE (the non-login flows' states carry a prefix) — reliable across the
  // redirect, unlike client-side storage.
  const stateParam = params.get("state") ?? ""
  const isCorpEsi = stateParam.startsWith(STRUCTURE_STATE_PREFIX)
  const isWallet = stateParam.startsWith(WALLET_STATE_PREFIX)

  useEffect(() => {
    if (handled.current) return
    handled.current = true

    const code = params.get("code")
    const state = params.get("state")
    if (!code || !state) {
      setError("Missing code or state in callback URL.")
      return
    }

    if (isCorpEsi) {
      completeStructureAuthorize(code, state)
        .then(async (status) => {
          // Connecting also (best-effort) populates the roster server-side, so
          // refresh both panels' data on return to Config.
          await queryClient.invalidateQueries({ queryKey: ["structureStatus"] })
          await queryClient.invalidateQueries({ queryKey: ["rosterStatus"] })
          // If the picker switched the authorizing character, pass the previous
          // name so the Corp ESI access panel can warn about the swap.
          const replaced = status?.replaced_character_name
          const q = replaced
            ? `&replaced=${encodeURIComponent(replaced)}`
            : ""
          navigate(`/config?authorized=corp-esi${q}`, { replace: true })
        })
        .catch((e) => setError((e as Error).message))
      return
    }

    if (isWallet) {
      completeWalletAuthorize(code, state)
        .then(async () => {
          await queryClient.invalidateQueries({ queryKey: ["walletStatus"] })
          navigate("/admin", { replace: true })
        })
        .catch((e) => setError((e as Error).message))
      return
    }

    login(code, state)
      .then(() => queryClient.invalidateQueries({ queryKey: ["me"] }))
      .then(() => navigate("/", { replace: true }))
      .catch((e) => setError((e as Error).message))
  }, [params, navigate, queryClient, isCorpEsi, isWallet])

  const verb = isCorpEsi
    ? "Connecting corp ESI access"
    : isWallet
      ? "Connecting the payment wallet"
      : "Signing you in"
  return (
    <main className="container">
      <section className="login-hero">
        <article className="login-panel">
          <p className="login-eyebrow">
            {error ? "Uplink Failed" : "Establishing Uplink"}
          </p>
          {error ? (
            <p className="error">
              {verb} failed: {error}
            </p>
          ) : (
            <p aria-busy="true">{verb}…</p>
          )}
        </article>
      </section>
    </main>
  )
}
