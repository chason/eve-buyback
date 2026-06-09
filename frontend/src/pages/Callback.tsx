import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { login } from "../api/auth"
import {
  completeStructureAuthorize,
  STRUCTURE_AUTH_FLAG,
} from "../api/structures"

export default function Callback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const handled = useRef(false)
  // The same SSO callback completes either a login or a structure-access grant.
  const isStructure = sessionStorage.getItem(STRUCTURE_AUTH_FLAG) === "1"

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
      sessionStorage.removeItem(STRUCTURE_AUTH_FLAG)
      completeStructureAuthorize(code, state)
        .then(() =>
          queryClient.invalidateQueries({ queryKey: ["structureStatus"] }),
        )
        .then(() => navigate("/config", { replace: true }))
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
