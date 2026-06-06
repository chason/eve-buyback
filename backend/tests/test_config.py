import pytest
from pydantic import ValidationError

from app.config import INSECURE_SESSION_SECRET, Settings


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
