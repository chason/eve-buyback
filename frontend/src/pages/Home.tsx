import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  beginLogin,
  getMe,
  logout,
  registerCorporation,
  type SessionUser,
} from "../api/auth"
import { getHealth } from "../api/health"

function CorporationStatus({ user }: { user: SessionUser }) {
  const queryClient = useQueryClient()
  const register = useMutation({
    mutationFn: registerCorporation,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["me"] }),
  })

  if (user.corporation_registered) {
    return (
      <p>
        Corp <strong>{user.corporation_name}</strong> is registered. Your role:{" "}
        <code>{user.role}</code>
      </p>
    )
  }

  const canRegister = user.role === "ceo" || user.is_director
  if (canRegister) {
    return (
      <div>
        <p>
          <strong>{user.corporation_name}</strong> isn't registered yet.
        </p>
        <button onClick={() => register.mutate()} disabled={register.isPending}>
          Register {user.corporation_name}
        </button>
        {register.isError && (
          <p style={{ color: "crimson" }}>{(register.error as Error).message}</p>
        )}
      </div>
    )
  }

  return (
    <p>
      Your corporation ({user.corporation_name}) isn't registered yet. Ask your CEO or
      a Director to register it.
    </p>
  )
}

export default function Home() {
  const queryClient = useQueryClient()
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth })
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })

  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["me"] }),
  })

  async function startLogin() {
    const { authorization_url } = await beginLogin()
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
              <CorporationStatus user={me.data} />
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
