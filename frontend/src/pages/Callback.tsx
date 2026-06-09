import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

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
  // The same SSO callback completes either a login or a structure-access grant.
  // Route on the OAuth `state` echoed back by EVE (the structure flow's state
  // carries a prefix) — reliable across the redirect, unlike client-side storage.
  const isStructure = (params.get("state") ?? "").startsWith(STRUCTURE_STATE_PREFIX)

  useEffect(() => {
    if (handled.current) return
    handled.current = true

    const code = params.get("code")
    const state = params.get("state")
    if (!code || !state) {
      setError("Missing code or state in callback URL.")
      return
    }

    if (isStructure) {
      completeStructureAuthorize(code, state)
        .then(async (status) => {
          await queryClient.invalidateQueries({ queryKey: ["structureStatus"] })
          // Carry the intent so Config re-selects the structure hub (the saved
          // config still points at the old hub until they pick + save a structure).
          // If the picker switched the authorizing character, pass the previous
          // name so Config can warn about the swap.
          const replaced = status?.replaced_character_name
          const q = replaced
            ? `&replaced=${encodeURIComponent(replaced)}`
            : ""
          navigate(`/config?authorized=structure${q}`, { replace: true })
        })
        .catch((e) => setError((e as Error).message))
      return
    }

    login(code, state)
      .then(() => queryClient.invalidateQueries({ queryKey: ["me"] }))
      .then(() => navigate("/", { replace: true }))
      .catch((e) => setError((e as Error).message))
  }, [params, navigate, queryClient, isStructure])

  const verb = isStructure ? "Authorizing structure access" : "Signing you in"
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      {error ? (
        <p style={{ color: "crimson" }}>{verb} failed: {error}</p>
      ) : (
        <p>{verb}…</p>
      )}
    </main>
  )
}
