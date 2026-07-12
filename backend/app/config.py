from decimal import Decimal
from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder signing key shipped for local dev only. Booting with this outside
# development would let anyone forge a session cookie (e.g. role="ceo").
INSECURE_SESSION_SECRET = "dev-insecure-change-me"

# Placeholder Fernet key for local dev only — a valid key so the cipher constructs,
# but public, so it must NOT be used to encrypt real refresh tokens in production
# (ADR-0029). Generate a real one: `python -c "from cryptography.fernet import
# Fernet; print(Fernet.generate_key().decode())"`.
INSECURE_TOKEN_KEY = "YnV5YmFjay1kZXYtaW5zZWN1cmUtdG9rZW4ta2V5ISE="


def _is_valid_fernet_key(key: str) -> bool:
    """Whether the string is a structurally valid Fernet key (32 url-safe-base64
    bytes). Checked at boot: a malformed `BUYBACK_TOKEN_ENCRYPTION_KEY` (e.g. a
    `token_urlsafe` secret) must refuse to start, not 500 at encrypt time."""
    try:
        Fernet(key.encode())
    except (ValueError, TypeError):
        return False
    return True


class Settings(BaseSettings):
    """Application settings, loaded from environment / .env (prefix BUYBACK_)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="BUYBACK_", extra="ignore"
    )

    app_name: str = "buyback"
    # Defaults to "production" so the security guards (Secure cookies, a real
    # session secret, real structure-token key) fail **closed**: an unset or
    # unknown BUYBACK_ENVIRONMENT is treated as production. Local dev must opt in
    # explicitly with BUYBACK_ENVIRONMENT=development (see .env.example) — only
    # that exact value relaxes the guards (#25).
    environment: str = "production"

    # Path to the built SPA (frontend/dist). When set and present, the backend
    # serves it alongside /api/v1 as a single deployable (ADR-0012); empty in
    # development, where the Vite dev server serves the SPA and proxies /api.
    static_dir: str = ""

    # PostgreSQL via asyncpg (ADR-0024). Override per environment in .env.
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/buyback"
    )

    # ESI compatibility date (YYYY-MM-DD): ESI now versions behaviour by date rather than
    # by URL route. We send this as the `X-Compatibility-Date` header on every ESI call and
    # use unversioned paths (no `/latest/` or `/vN/`). It pins the API behaviour to the date
    # our integration was last validated against the changelog — bump it deliberately after
    # reviewing changes, never silently. See the eve-esi skill.
    esi_compatibility_date: str = "2026-06-01"

    # Default market hub: Jita 4-4 station (ADR-0006).
    market_hub_id: int = 60003760
    # How long a cached market price stays fresh before re-fetch (ADR-0006).
    market_cache_ttl_seconds: int = 60 * 60  # 1 hour
    # Max concurrent ESI region-order requests (one request per type; ADR-0028). This is
    # a **process-wide** cap shared by all in-flight appraisals + the background refresh
    # (ADR-0035), so concurrent requests can't multiply outbound load. Keep modest to
    # respect ESI's per-IP error budget.
    esi_market_concurrency: int = 8
    # Cap on distinct non-Fuzzwork (ESI-priced) types in a single appraisal (ADR-0035).
    # Each such type is a separate live ESI lookup, so this bounds the worst-case outbound
    # fan-out of one request against the cold-type cache-miss attack (#23). Fuzzwork hubs
    # batch into one request and are bounded only by the 1000-item cap.
    max_esi_types_per_appraisal: int = 100

    # Background refresh of non-Fuzzwork hub prices (ADR-0034): a scheduled job keeps
    # ESI-priced hubs (non-Jita NPC stations, player structures) warm so appraisals
    # there don't pay the slow ESI fetch. A pure-Fuzzwork (Jita) deploy has no such
    # hubs and the job is a no-op. The window renewed each cycle is
    # market_cache_ttl_seconds - market_refresh_interval_seconds (clamped at 0).
    market_background_refresh_enabled: bool = True
    market_refresh_interval_seconds: int = 600  # 10 minutes
    market_refresh_initial_delay_seconds: int = 30  # first run after boot

    # Background refresh of corp rosters (ADR-0036): a daily job re-pulls each
    # token-holding corp's member list (via the persisted Corp ESI token) so the
    # manager-designation picker stays current without anyone clicking. The manual
    # "Refresh roster" button is rate-limited to roster_manual_refresh_min_interval_seconds;
    # the background job ignores that cooldown.
    roster_background_refresh_enabled: bool = True
    roster_refresh_interval_seconds: int = 86400  # 24 hours
    roster_refresh_initial_delay_seconds: int = 60  # first run after boot
    roster_manual_refresh_min_interval_seconds: int = 900  # 15 min manual cooldown

    # Background contract watcher (ADR-0037): a job polls each token-holding corp's EVE
    # contracts (via the Corp ESI token) and reflects matched-contract status on appraisals.
    contracts_background_refresh_enabled: bool = True
    contracts_refresh_interval_seconds: int = 900  # 15 minutes
    contracts_refresh_initial_delay_seconds: int = 90  # first run after boot

    # Paid accounting add-on billing (ADR-0042): the recurring ISK price per access
    # period, and the reconciliation job that reads the OPERATOR's wallet journal
    # (their own character token, distinct from any tenant corp token) and extends
    # entitlements when a matching payment arrives. The job no-ops until an app admin
    # connects the operator wallet.
    accounting_price_isk: int = 250_000_000  # ISK per access period
    accounting_period_days: int = 30
    # "Sitting a while" threshold for the inventory view (ADR-0043, #152): lots held
    # at least this many days are flagged as going stale in "What we've got".
    accounting_stale_days: int = 30
    # Sales-tax rate netted out of the current market value when estimating what stock
    # would fetch today (NRV, ADR-0043/#153). Defaults to EVE's base 4.5% — deliberately
    # the untrained-skills worst case, so the paper number errs conservative. Operators
    # can lower it to match their traders' Accounting skill.
    accounting_sales_tax_rate: Decimal = Decimal("0.045")
    # Automatic write-down sweep (ADR-0043, #153): for every corp with the accounting
    # add-on, lots worth less than their carried cost are written down to market value
    # and the loss booked — conservatism, applied without anyone clicking. Daily.
    accounting_writedown_enabled: bool = True
    accounting_writedown_interval_seconds: int = 86400  # 24 hours
    accounting_writedown_initial_delay_seconds: int = 300
    payments_background_refresh_enabled: bool = True
    payments_refresh_interval_seconds: int = 1800  # 30 minutes
    payments_refresh_initial_delay_seconds: int = 120  # first run after boot

    # Pluggable L1 cache in front of the market_prices DB cache (ADR-0033). Default
    # is an in-process LRU; set "memcached" + the address to share across processes.
    # SECURITY: memcached is UNAUTHENTICATED — bind it to loopback or a private/
    # firewalled network. Anything that can reach the port can read/poison cached
    # prices (L1 hits win over the DB and source). Never expose 11211 publicly.
    cache_backend: Literal["memory", "memcached"] = "memory"
    memcached_addr: str = "localhost:11211"  # host:port
    # Per-op timeout for the memcached backend; on timeout the L1 op degrades to a
    # miss/no-op (best-effort), so a slow node can't stall requests.
    memcached_timeout_seconds: float = 1.0
    # L1 freshness — enforced ≤ market_cache_ttl_seconds at boot (the durable DB tier).
    market_l1_cache_ttl_seconds: int = 60
    cache_max_entries: int = 10_000  # MemoryCache LRU bound

    # Session cookie signing (ADR-0004). CHANGE in production.
    session_secret: str = INSECURE_SESSION_SECRET
    session_cookie_name: str = "buyback_session"
    session_max_age: int = 60 * 60 * 8  # 8 hours

    # EVE SSO (ADR-0004). Register an app at https://developers.eveonline.com/.
    eve_client_id: str = ""
    eve_client_secret: str = ""
    eve_redirect_uri: str = "http://localhost:5173/auth/callback"
    # Login scopes: publicData + the roles scope (detect Directors for registration,
    # ADR-0015) + the open-window scope, which lets a manager open a matched contract in
    # their own EVE client (ADR-0038). The login refresh token is kept encrypted in the
    # session to power that (amends ADR-0004); users who logged in earlier re-login to gain
    # the scope.
    eve_scopes: str = (
        "publicData esi-characters.read_corporation_roles.v1 esi-ui.open_window.v1"
    )
    # Scopes requested by the separate "authorize structure access" flow (ADR-0029):
    # read the structure's market, search structures by name, and resolve their names.
    # (ESI structure scopes have no ".read" suffix.)
    eve_structure_scopes: str = (
        "publicData esi-markets.structure_markets.v1 "
        "esi-search.search_structures.v1 esi-universe.read_structures.v1"
    )
    # Membership scope, folded into the Corp ESI access grant (ADR-0036) so the corp
    # roster can be pulled for the manager-designation picker. Kept off normal login so
    # ordinary members never consent to it.
    eve_roster_scopes: str = "publicData esi-corporations.read_corporation_membership.v1"
    # Corp-contracts scope, folded into the Corp ESI access grant (ADR-0037) so the
    # background watcher can read the corp's contracts. Existing tokens granted before
    # this must reconnect to gain it.
    eve_contracts_scopes: str = "esi-contracts.read_corporation_contracts.v1"
    # Scopes for the OPERATOR wallet grant (ADR-0042): reading the operator's own
    # character wallet journal to reconcile incoming ISK access payments. This token
    # belongs to the instance operator, never to a tenant corp.
    eve_wallet_scopes: str = "publicData esi-wallet.read_character_wallet.v1"

    @property
    def eve_corp_token_scopes(self) -> str:
        """The full scope set for the one persisted Corp ESI access token (ADR-0036,
        0037): structure-market access + corp-membership + corp-contracts, requested in a
        single grant. Deduped, order-preserving (the sets share `publicData`)."""
        seen: dict[str, None] = {}
        combined = (
            f"{self.eve_structure_scopes} {self.eve_roster_scopes} "
            f"{self.eve_contracts_scopes}"
        )
        for scope in combined.split():
            seen.setdefault(scope, None)
        return " ".join(seen)

    # Instance **app-admin** allowlist (ADR-0041): comma-separated EVE character ids that
    # operate this hosted instance — an authorization axis orthogonal to the per-corp
    # member/manager/ceo roles. Empty by default (no admins). Read only through
    # `admin_character_id_set`; a non-numeric entry refuses to boot. Not a token/secret,
    # just an id list — so no encryption and no Privacy-page implication.
    admin_character_ids: str = ""

    # Fernet key encrypting persisted structure-market refresh tokens at rest
    # (ADR-0029). Required (a real value) to use structure hubs in production.
    token_encryption_key: str = INSECURE_TOKEN_KEY

    @property
    def session_https_only(self) -> bool:
        """Require Secure cookies outside local development."""
        return self.environment != "development"

    @property
    def admin_character_id_set(self) -> frozenset[int]:
        """The parsed app-admin allowlist (ADR-0041) as EVE character ids. Blanks are
        ignored; entries are validated numeric at boot (`_require_numeric_admin_ids`)."""
        return frozenset(
            int(part) for part in self.admin_character_ids.split(",") if part.strip()
        )

    @property
    def corp_esi_token_configured(self) -> bool:
        """Whether a real token-encryption key is set. Structure auth is refused
        outside development while this is the public placeholder (ADR-0029). The
        key's structural validity is enforced at boot (`_require_valid_token_key`),
        so a True here means the cipher actually works."""
        if self.environment == "development":
            return True
        key = self.token_encryption_key.strip()
        return bool(key) and key != INSECURE_TOKEN_KEY

    @model_validator(mode="after")
    def _require_secure_session_secret(self) -> "Settings":
        """Refuse to boot with the placeholder/empty signing key outside dev."""
        if self.environment != "development" and (
            not self.session_secret.strip()
            or self.session_secret == INSECURE_SESSION_SECRET
        ):
            raise ValueError(
                "BUYBACK_SESSION_SECRET must be set to a strong, unique value when "
                "BUYBACK_ENVIRONMENT is not 'development'. The placeholder default "
                "would let anyone forge a session cookie."
            )
        return self

    @model_validator(mode="after")
    def _require_numeric_admin_ids(self) -> "Settings":
        """Refuse to boot if `BUYBACK_ADMIN_CHARACTER_IDS` has a non-numeric entry — an
        operator typo would otherwise silently grant no one (or 500 at request time when
        `admin_character_id_set` parses)."""
        for part in self.admin_character_ids.split(","):
            part = part.strip()
            if part and not part.isdigit():
                raise ValueError(
                    "BUYBACK_ADMIN_CHARACTER_IDS must be a comma-separated list of EVE "
                    f"character ids (digits only); got invalid entry {part!r}."
                )
        return self

    @model_validator(mode="after")
    def _require_valid_token_key(self) -> "Settings":
        """Refuse to boot with a malformed token-encryption key (any environment).

        An invalid key (including empty) can never construct the cipher — failing at
        startup puts the error in the deploy logs instead of 500ing requests at use
        time. Structures stay optional (ADR-0029): leaving the variable **unset**
        keeps the valid dev-placeholder default, which boots fine and is simply
        refused for authorization outside development.
        """
        if not _is_valid_fernet_key(self.token_encryption_key.strip()):
            raise ValueError(
                "BUYBACK_TOKEN_ENCRYPTION_KEY is not a valid Fernet key (44 chars, "
                "url-safe base64). Generate one with: python -c \"from "
                "cryptography.fernet import Fernet; "
                'print(Fernet.generate_key().decode())" — or unset it entirely if '
                "you don't price at player structures."
            )
        return self

    @model_validator(mode="after")
    def _require_l1_ttl_within_db_ttl(self) -> "Settings":
        """The L1 cache (ADR-0033) must not outlive the durable DB freshness window —
        an L1 hit is served without an `is_fresh` check, so a longer L1 TTL would serve
        prices staler than the DB tier permits. Fail at boot rather than silently."""
        if self.market_l1_cache_ttl_seconds > self.market_cache_ttl_seconds:
            raise ValueError(
                "BUYBACK_MARKET_L1_CACHE_TTL_SECONDS "
                f"({self.market_l1_cache_ttl_seconds}) must be ≤ "
                "BUYBACK_MARKET_CACHE_TTL_SECONDS "
                f"({self.market_cache_ttl_seconds}); a longer L1 TTL would serve "
                "prices staler than the durable DB cache allows (ADR-0033)."
            )
        return self

    @model_validator(mode="after")
    def _require_valid_memcached_addr(self) -> "Settings":
        """Parse `memcached_addr` at boot when the memcached backend is selected, so a
        bad host:port fails in the deploy logs instead of raising inside the app
        lifespan (which would leak the already-created HTTP client)."""
        if self.cache_backend != "memcached":
            return self
        _, sep, port = self.memcached_addr.partition(":")
        if sep and port:
            try:
                p = int(port)
            except ValueError:
                p = -1
            if not (1 <= p <= 65535):
                raise ValueError(
                    "BUYBACK_MEMCACHED_ADDR must be 'host' or 'host:port' with a port "
                    f"in 1..65535; got {self.memcached_addr!r}."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
