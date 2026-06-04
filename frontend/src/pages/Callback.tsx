import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { login } from "../api/auth"

export default function Callback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const handled = useRef(false)

  useEffect(() => {
    if (handled.current) return
    handled.current = true

    const code = params.get("code")
    const state = params.get("state")
    if (!code || !state) {
      setError("Missing code or state in callback URL.")
      return
    }

    login(code, state)
      .then(() => queryClient.invalidateQueries({ queryKey: ["me"] }))
      .then(() => navigate("/", { replace: true }))
      .catch((e) => setError((e as Error).message))
  }, [params, navigate, queryClient])

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      {error ? (
        <p style={{ color: "crimson" }}>Login failed: {error}</p>
      ) : (
        <p>Signing you in…</p>
      )}
    </main>
  )
}
