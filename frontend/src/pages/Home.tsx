import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { getLoginUrl, getMe, logout } from "../api/auth"
import { getHealth } from "../api/health"

export default function Home() {
  const queryClient = useQueryClient()
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth })
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })

  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["me"] }),
  })

  async function startLogin() {
    const { authorization_url } = await getLoginUrl()
    window.location.href = authorization_url
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 640 }}>
      <h1>Buyback</h1>
      <p>EVE Online corporation buyback — scaffold.</p>

      <section>
        <h2>Session</h2>
        {me.isLoading && <p>Checking session…</p>}
        {me.isError && <p style={{ color: "crimson" }}>Could not load session.</p>}
        {!me.isLoading && !me.isError && (
          me.data ? (
            <div>
              <p>
                Logged in as <strong>{me.data.character_name}</strong>
              </p>
              <p>
                Corp: {me.data.corporation_name} · Role: <code>{me.data.role}</code>
              </p>
              <button onClick={() => logoutMutation.mutate()} disabled={logoutMutation.isPending}>
                Log out
              </button>
            </div>
          ) : (
            <button onClick={startLogin}>Log in with EVE Online</button>
          )
        )}
      </section>

      <section>
        <h2>Backend health</h2>
        {health.isLoading && <p>Checking…</p>}
        {health.isError && (
          <p style={{ color: "crimson" }}>Error: {(health.error as Error).message}</p>
        )}
        {health.data && (
          <pre style={{ background: "#f4f4f4", padding: "0.75rem", borderRadius: 6 }}>
            status: {health.data.status}
            {"\n"}database: {health.data.database}
          </pre>
        )}
      </section>
    </main>
  )
}
