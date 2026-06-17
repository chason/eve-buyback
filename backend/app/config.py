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
    # Roles scope lets us detect Directors for corp registration (ADR-0015).
    eve_scopes: str = "publicData esi-characters.read_corporation_roles.v1"
    # Scopes requested by the separate "authorize structure access" flow (ADR-0029):
    # read the structure's market, search structures by name, and resolve their names.
    # (ESI structure scopes have no ".read" suffix.)
    eve_structure_scopes: str = (
        "publicData esi-markets.structure_markets.v1 "
        "esi-search.search_structures.v1 esi-universe.read_structures.v1"
    )

    # Fernet key encrypting persisted structure-market refresh tokens at rest
    # (ADR-0029). Required (a real value) to use structure hubs in production.
    token_encryption_key: str = INSECURE_TOKEN_KEY

    @property
    def session_https_only(self) -> bool:
        """Require Secure cookies outside local development."""
        return self.environment != "development"

    @property
    def structure_tokens_configured(self) -> bool:
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
