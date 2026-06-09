from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder signing key shipped for local dev only. Booting with this outside
# development would let anyone forge a session cookie (e.g. role="ceo").
INSECURE_SESSION_SECRET = "dev-insecure-change-me"


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
    # How long a cached Fuzzwork price stays fresh before re-fetch (ADR-0006).
    market_cache_ttl_seconds: int = 60 * 60  # 1 hour

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

    @property
    def session_https_only(self) -> bool:
        """Require Secure cookies outside local development."""
        return self.environment != "development"

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
