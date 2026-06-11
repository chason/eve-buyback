from functools import lru_cache

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
    bytes). A malformed `BUYBACK_TOKEN_ENCRYPTION_KEY` (e.g. a `token_urlsafe`
    secret) must read as *not configured* rather than blow up at encrypt time."""
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
    environment: str = "development"

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
    # Max concurrent ESI region-order requests when pricing a non-Fuzzwork hub
    # (one request per type; ADR-0028). Keep modest to respect ESI's error budget.
    esi_market_concurrency: int = 8

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
        """Whether a *usable* token-encryption key is set: structurally a valid
        Fernet key, and outside development not the public placeholder (ADR-0029).
        Structure auth is refused with a clean error while this is False."""
        key = self.token_encryption_key.strip()
        if not _is_valid_fernet_key(key):
            return False
        if self.environment == "development":
            return True
        return key != INSECURE_TOKEN_KEY

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
