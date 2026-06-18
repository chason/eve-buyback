import { Link } from "react-router-dom"

// A user-facing, plain-language summary of how the app handles EVE data and tokens
// (#112). Kept accurate to the ADRs it cites — those are the source of truth for token
// handling. Public (no auth) so members can read it before granting any access.

// The app is open source; the cited ADRs link to their source on GitHub for full detail.
const REPO_URL = "https://github.com/chason/eve-buyback"
const ADR_BASE = `${REPO_URL}/blob/main/docs/adr`
const ADRS: { id: string; file: string }[] = [
  { id: "ADR-0004", file: "0004-eve-sso-session-auth.md" },
  { id: "ADR-0015", file: "0015-corp-registration-ceo-or-director.md" },
  { id: "ADR-0016", file: "0016-per-request-role-resolution.md" },
  { id: "ADR-0029", file: "0029-encrypted-refresh-token-structures.md" },
  { id: "ADR-0036", file: "0036-corp-roster-manager-designation.md" },
  { id: "ADR-0037", file: "0037-corp-contract-watcher.md" },
  { id: "ADR-0038", file: "0038-open-in-eve-login-token.md" },
]

export default function Privacy() {
  return (
    <>
      <hgroup>
        <h1>Privacy &amp; data use</h1>
        <p>
          What EVE data this app reads, what it stores, and for how long. The
          authoritative details live in the project&apos;s architecture decision
          records (
          {ADRS.map((adr, i) => (
            <span key={adr.id}>
              {i > 0 && ", "}
              <a
                href={`${ADR_BASE}/${adr.file}`}
                target="_blank"
                rel="noreferrer"
              >
                {adr.id}
              </a>
            </span>
          ))}
          ). The app is{" "}
          <a href={REPO_URL} target="_blank" rel="noreferrer">
            open source on GitHub
          </a>
          .
        </p>
      </hgroup>

      <div className="panel">
        <section>
          <h2>Signing in</h2>
          <p>
            You authenticate through <strong>EVE Online SSO</strong> — we never see
            your password. The scopes requested at login are <code>publicData</code>,{" "}
            <code>read_corporation_roles</code> (to tell whether you&apos;re a CEO or
            Director), and <code>esi-ui.open_window.v1</code> (to open a matched contract
            in your EVE client — see below).
          </p>
          <p>
            <strong>We store no login token on our servers.</strong> After login the app
            issues a signed, http-only session cookie holding your character and
            corporation id and name and whether you&apos;re a CEO/Director. The access
            token is discarded; your <strong>refresh token is kept encrypted inside that
            cookie</strong> (never in our database) so you can open a contract in EVE —
            nothing else. The session lasts about 8 hours, then you sign in again; your
            role is re-checked on every request, so role changes take effect immediately.
          </p>
        </section>

        <section>
          <h2>Detecting CEO / Director</h2>
          <p>
            The <code>read_corporation_roles</code> scope is read <strong>once at
            login</strong> to detect whether you&apos;re a Director (the CEO is public
            information). Only the resulting flag is kept in your session cookie — we
            don&apos;t re-read your roles after login.
          </p>
        </section>

        <section>
          <h2>Corp ESI access (structure markets + roster)</h2>
          <p>
            This is the one token the app stores in its <strong>database</strong>, and
            it&apos;s <strong>opt-in</strong>: a CEO or Director connects it on the Config
            page,
            only if the corp prices at player structures or wants member search for
            designating managers.
          </p>
          <ul>
            <li>
              It&apos;s <strong>one token per corporation</strong>, carrying the scopes
              to read a structure&apos;s market orders, search and resolve structures,
              read the corporation&apos;s member list, and read the corporation&apos;s
              contracts.
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
          <h2>Contract tracking</h2>
          <p>
            When the corp ESI token can read contracts, the app checks the
            corporation&apos;s <strong>item-exchange contracts</strong> in the background
            (about every 15 minutes) and matches each to its appraisal — by the appraisal
            id a member pastes into the contract description — so an appraisal can show
            whether its contract is in progress, completed, or doesn&apos;t match. It
            reads contracts made to or by the <strong>corporation</strong>, not your
            personal contracts elsewhere.
          </p>
          <p>
            All it keeps per appraisal is the matched{" "}
            <strong>contract id, its status, and the issue/complete timestamps</strong> —
            no extra item or ISK detail beyond what the appraisal already holds. If the
            token lacks the contracts scope, or the character lacks the in-game role to
            read corp contracts, the app simply skips this and tracks nothing.
          </p>
        </section>

        <section>
          <h2>Opening a contract in EVE</h2>
          <p>
            When an appraisal has a matched contract, an <strong>Open in EVE</strong>{" "}
            button opens it in your running EVE client. To do that the app uses the{" "}
            <strong>refresh token kept encrypted in your session cookie</strong> (above):
            on each click it gets a short-lived access token and asks EVE to open that one
            contract window — it makes <strong>no other call</strong> with your login token.
          </p>
          <p>
            The token never leaves your cookie for our database, and is dropped when your
            session ends or you sign out. If you signed in before this feature, the button
            stays hidden until you log in again to grant the <code>open_window</code> scope.
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
          <p>Except for what&apos;s detailed above, we never:</p>
          <ul>
            <li>store any EVE token on our servers, except the one opt-in corp token
              (the login refresh token stays only in your own encrypted cookie);</li>
            <li>read your wallet, assets, mail, or skills;</li>
            <li>read your personal contracts — only the corporation&apos;s item-exchange
              contracts, via the opt-in corp token described above;</li>
            <li>sell your data or share it with third parties.</li>
          </ul>
        </section>

        <section>
          <h2>Revoking access &amp; deleting data</h2>
          <ul>
            <li>
              Disconnect corp ESI access from the <Link to="/config">Config</Link> page —
              structure pricing stops, the roster snapshot goes stale, and contract
              tracking stops.
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
