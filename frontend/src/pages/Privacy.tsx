import { Link } from "react-router-dom"

// A user-facing, plain-language summary of how the app handles EVE data and tokens
// (#112). Kept accurate to the ADRs it cites — those are the source of truth for token
// handling (docs/adr/0004, 0015, 0016, 0029, 0036). Public (no auth) so members can
// read it before granting any access.
export default function Privacy() {
  return (
    <>
      <hgroup>
        <h1>Privacy &amp; data use</h1>
        <p>
          What EVE data this app reads, what it stores, and for how long. The
          authoritative details live in the project&apos;s architecture decision
          records (ADR-0004, 0015, 0016, 0029, 0036).
        </p>
      </hgroup>

      <div className="panel">
        <section>
          <h2>Signing in</h2>
          <p>
            You authenticate through <strong>EVE Online SSO</strong> — we never see
            your password. The only scopes requested at login are{" "}
            <code>publicData</code> and <code>read_corporation_roles</code> (used to
            tell whether you&apos;re a CEO or Director).
          </p>
          <p>
            <strong>We do not store your EVE login token.</strong> After login the app
            issues a signed, http-only session cookie holding only your character and
            corporation id and name, plus whether you&apos;re a CEO/Director. The access
            token used at login is discarded. The session lasts about 8 hours, then you
            sign in again; your role is re-checked on every request, so role changes take
            effect immediately.
          </p>
        </section>

        <section>
          <h2>Detecting CEO / Director</h2>
          <p>
            The <code>read_corporation_roles</code> scope is read <strong>once at
            login</strong> to detect whether you&apos;re a Director (the CEO is public
            information). The result is a single flag in your session cookie — the token
            itself isn&apos;t kept.
          </p>
        </section>

        <section>
          <h2>Corp ESI access (structure markets + roster)</h2>
          <p>
            This is the one place the app stores a token, and it&apos;s{" "}
            <strong>opt-in</strong>: a CEO or Director connects it on the Config page,
            only if the corp prices at player structures or wants member search for
            designating managers.
          </p>
          <ul>
            <li>
              It&apos;s <strong>one token per corporation</strong>, carrying the scopes
              to read a structure&apos;s market orders, search and resolve structures,
              and read the corporation&apos;s member list.
            </li>
            <li>
              The <strong>refresh token is encrypted at rest</strong> (Fernet) before it
              touches the database — only the ciphertext is stored. Short-lived access
              tokens are generated transiently server-side and are never persisted.
            </li>
            <li>
              A corp that prices only at public hubs (Jita via Fuzzwork, NPC regions)
              never connects this and stores no token at all.
            </li>
            <li>
              It can be <strong>revoked at any time</strong> from the Config page (or by
              removing the application at EVE SSO).
            </li>
          </ul>
        </section>

        <section>
          <h2>The roster snapshot</h2>
          <p>
            When the corp ESI token can read membership, the app caches a member-list
            snapshot — <strong>character names and ids only</strong> — so managers can
            search the roster when designating a Buyback Manager. It refreshes
            automatically about once a day (and via a manual, rate-limited refresh
            button). It&apos;s a search convenience, not an authority: designations are
            still checked against EVE.
          </p>
        </section>

        <section>
          <h2>Market price data</h2>
          <p>
            Quote prices come from <strong>Fuzzwork aggregates and EVE ESI</strong>,
            cached briefly (in memory, and up to about an hour in the database) so quotes
            are fast. This is public market data, identical for everyone and shared
            across corps using the same hub — <strong>not your personal data</strong>.
          </p>
        </section>

        <section>
          <h2>What we never do</h2>
          <ul>
            <li>We never store your EVE login or access token.</li>
            <li>
              We never read your wallet, contracts, assets, mail, or skills — only the
              scopes listed above.
            </li>
            <li>We never sell your data or share it with third parties.</li>
          </ul>
        </section>

        <section>
          <h2>Revoking access &amp; deleting data</h2>
          <ul>
            <li>
              Disconnect corp ESI access from the <Link to="/config">Config</Link> page —
              structure pricing stops and the roster snapshot goes stale.
            </li>
            <li>
              Removing the application from your EVE account management (Authorized
              Applications) revokes the token at the source.
            </li>
            <li>
              Your session is just a cookie — signing out or letting it expire ends it;
              there&apos;s no server-side login store.
            </li>
          </ul>
        </section>
      </div>
    </>
  )
}
