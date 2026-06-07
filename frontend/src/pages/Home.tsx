import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { beginLogin, getMe, registerCorporation, type SessionUser } from "../api/auth"

function CorporationStatus({ user }: { user: SessionUser }) {
  const queryClient = useQueryClient()
  const register = useMutation({
    mutationFn: registerCorporation,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["me"] }),
  })

  if (user.corporation_registered) {
    return (
      <p>
        <Link to="/appraise" role="button">
          Start an appraisal
        </Link>
      </p>
    )
  }

  if (user.role === "ceo" || user.is_director) {
    return (
      <article>
        <p>
          <strong>{user.corporation_name}</strong> isn't registered yet.
        </p>
        <button
          onClick={() => register.mutate()}
          aria-busy={register.isPending}
        >
          Register {user.corporation_name}
        </button>
        {register.isError && (
          <p className="error">{(register.error as Error).message}</p>
        )}
      </article>
    )
  }

  return (
    <p>
      Your corporation (<strong>{user.corporation_name}</strong>) isn't registered
      yet. Ask your CEO or a Director to register it.
    </p>
  )
}

export default function Home() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe })

  async function startLogin() {
    const { authorization_url } = await beginLogin()
    window.location.href = authorization_url
  }

  return (
    <>
      <hgroup>
        <h1>Buyback</h1>
        <p>EVE Online corporation buyback.</p>
      </hgroup>

      {me.isLoading && <p aria-busy="true">Checking session…</p>}
      {me.isError && <p className="error">Could not load session.</p>}
      {!me.isLoading &&
        !me.isError &&
        (me.data ? (
          <section>
            <p>
              Logged in as <strong>{me.data.character_name}</strong> — role{" "}
              <code>{me.data.role}</code>
            </p>
            <CorporationStatus user={me.data} />
          </section>
        ) : (
          <button onClick={startLogin}>Log in with EVE Online</button>
        ))}
    </>
  )
}
