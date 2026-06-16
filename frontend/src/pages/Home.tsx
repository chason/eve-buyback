import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { beginLogin, getMe, registerCorporation, type SessionUser } from "../api/auth"
import { roleLabel } from "../lib/roles"

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

      <section>
        <h2>How it works</h2>
        <div className="flow">
          <div className="flow-step">
            <span className="flow-num">1</span>
            <h3>Appraise</h3>
            <p>
              Paste your items (or search and add them) and submit to get a priced
              quote.
            </p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="flow-step">
            <span className="flow-num">2</span>
            <h3>Contract</h3>
            <p>
              Make an <em>item exchange</em> contract to your corporation for the
              quoted price. The appraisal page lists the exact fields to fill in.
            </p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="flow-step">
            <span className="flow-num">3</span>
            <h3>Get paid</h3>
            <p>A Buyback Manager reviews and accepts the contract, and you get paid.</p>
          </div>
        </div>
      </section>

      {me.isLoading && <p aria-busy="true">Checking session…</p>}
      {me.isError && <p className="error">Could not load session.</p>}
      {!me.isLoading &&
        !me.isError &&
        (me.data ? (
          <section>
            <p>
              Logged in as <strong>{me.data.character_name}</strong> — role{" "}
              <strong>{roleLabel(me.data.role)}</strong>
            </p>
            <CorporationStatus user={me.data} />
          </section>
        ) : (
          <button onClick={startLogin}>Log in with EVE Online</button>
        ))}
    </>
  )
}
