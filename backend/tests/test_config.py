import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import INSECURE_SESSION_SECRET, INSECURE_TOKEN_KEY, Settings


def test_placeholder_secret_rejected_in_production():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            session_secret=INSECURE_SESSION_SECRET,
            _env_file=None,
        )


def test_empty_secret_rejected_in_production():
    with pytest.raises(ValidationError):
        Settings(environment="production", session_secret="   ", _env_file=None)


def test_strong_secret_allowed_in_production():
    settings = Settings(
        environment="production",
        session_secret="a-real-strong-secret",
        _env_file=None,
    )
    assert settings.session_secret == "a-real-strong-secret"


def test_placeholder_allowed_in_development():
    settings = Settings(
        environment="development",
        session_secret=INSECURE_SESSION_SECRET,
        _env_file=None,
    )
    assert settings.session_secret == INSECURE_SESSION_SECRET


def test_unset_environment_fails_closed(monkeypatch):
    # An unset BUYBACK_ENVIRONMENT must NOT silently relax the guards (#25): the
    # default is production, so the placeholder secret refuses to boot.
    monkeypatch.delenv("BUYBACK_ENVIRONMENT", raising=False)
    settings = Settings(session_secret="a-real-strong-secret", _env_file=None)
    assert settings.environment == "production"
    assert settings.session_https_only is True
    with pytest.raises(ValidationError):
        Settings(session_secret=INSECURE_SESSION_SECRET, _env_file=None)


def test_unknown_environment_treated_as_production():
    # Only the exact value "development" relaxes the guards; anything else is
    # treated as production (fail-closed).
    with pytest.raises(ValidationError):
        Settings(
            environment="staging",
            session_secret=INSECURE_SESSION_SECRET,
            _env_file=None,
        )


# --- BUYBACK_TOKEN_ENCRYPTION_KEY is validated at startup (any environment) ---


def test_malformed_token_key_refuses_to_boot():
    # e.g. a token_urlsafe secret pasted where a Fernet key belongs
    with pytest.raises(ValidationError, match="not a valid Fernet key"):
        Settings(token_encryption_key="not-a-fernet-key", _env_file=None)


def test_empty_token_key_refuses_to_boot():
    with pytest.raises(ValidationError, match="not a valid Fernet key"):
        Settings(token_encryption_key="  ", _env_file=None)


def test_real_token_key_boots_and_is_configured_in_production():
    key = Fernet.generate_key().decode()
    settings = Settings(
        environment="production",
        session_secret="a-real-strong-secret",
        token_encryption_key=key,
        _env_file=None,
    )
    assert settings.structure_tokens_configured is True


def test_placeholder_token_key_boots_but_is_not_configured_in_production():
    # The valid dev-placeholder default boots (structures optional, ADR-0029) but
    # is refused for authorization outside development.
    settings = Settings(
        environment="production",
        session_secret="a-real-strong-secret",
        token_encryption_key=INSECURE_TOKEN_KEY,
        _env_file=None,
    )
    assert settings.structure_tokens_configured is False


# --- cache config validators (ADR-0033) ---


def _dev(**kw):
    # environment=development relaxes the secret/key guards so we isolate cache checks.
    return Settings(environment="development", _env_file=None, **kw)


def test_l1_ttl_must_not_exceed_db_ttl():
    with pytest.raises(ValidationError, match="L1_CACHE_TTL"):
        _dev(market_l1_cache_ttl_seconds=7200, market_cache_ttl_seconds=3600)


def test_l1_ttl_within_db_ttl_ok():
    s = _dev(market_l1_cache_ttl_seconds=60, market_cache_ttl_seconds=3600)
    assert s.market_l1_cache_ttl_seconds == 60


def test_bad_memcached_addr_rejected_when_selected():
    with pytest.raises(ValidationError, match="MEMCACHED_ADDR"):
        _dev(cache_backend="memcached", memcached_addr="localhost:not-a-port")


def test_bad_memcached_addr_ignored_when_memory_backend():
    # The address is only parsed when the memcached backend is actually selected.
    s = _dev(cache_backend="memory", memcached_addr="localhost:not-a-port")
    assert s.cache_backend == "memory"


def test_good_memcached_addr_accepted():
    assert _dev(cache_backend="memcached", memcached_addr="cache.host:11211")
    assert _dev(cache_backend="memcached", memcached_addr="cache.host")  # host only


def test_background_refresh_defaults():
    # On by default (ADR-0034) with a 10-minute cycle and a short initial delay.
    s = _dev()
    assert s.market_background_refresh_enabled is True
    assert s.market_refresh_interval_seconds == 600
    assert s.market_refresh_initial_delay_seconds == 30
