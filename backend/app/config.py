from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / .env (prefix BUYBACK_)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="BUYBACK_", extra="ignore"
    )

    app_name: str = "buyback"
    environment: str = "development"

    # Async SQLite by default; swap to PostgreSQL via this URL (ADR-0002).
    database_url: str = "sqlite+aiosqlite:///./buyback.db"

    # Default market hub: Jita 4-4 station (ADR-0006).
    market_hub_id: int = 60003760

    # Session cookie signing (ADR-0004). CHANGE in production.
    session_secret: str = "dev-insecure-change-me"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
