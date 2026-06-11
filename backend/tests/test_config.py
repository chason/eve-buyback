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
