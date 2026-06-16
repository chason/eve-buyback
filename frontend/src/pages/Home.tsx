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
          <section className="login-hero">
            <article className="login-panel">
              <p className="login-eyebrow">Capsuleer Access</p>
              <h2 className="login-title">Sign in to your corp buyback</h2>
              <p className="login-tagline">
                Authenticate with your EVE Online character to get instant,
                market-priced quotes and contract your haul to the corporation.
              </p>
              <button
                type="button"
                className="login-cta"
                onClick={startLogin}
              >
                <img
                  src="https://web.ccpgamescdn.com/eveonlineassets/developers/eve-sso-login-black-large.png"
                  alt="Log in with EVE Online"
                  width="270"
                  height="45"
                />
              </button>
              <p className="login-fineprint">
                We never see your password — login is handled by EVE Online SSO.
              </p>
            </article>
          </section>
        ))}
    </>
  )
}
