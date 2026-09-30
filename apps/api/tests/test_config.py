import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults_boot_without_database_or_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    settings = make()
    assert settings.database_url is None
    assert settings.anthropic_api_key is None


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test,")
    assert make().cors_origins == ["http://a.test", "http://b.test"]


def test_database_url_must_use_asyncpg() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        make(database_url="postgresql://u:p@localhost/db")


def test_secrets_are_not_leaked_in_repr() -> None:
    settings = make(
        anthropic_api_key="sk-test-secret",
        database_url="postgresql+asyncpg://u:hunter2@localhost/db",
    )
    rendered = repr(settings)
    assert "sk-test-secret" not in rendered
    assert "hunter2" not in rendered
