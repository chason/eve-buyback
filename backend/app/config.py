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


@lru_cache
def get_settings() -> Settings:
    return Settings()
