"""Settings for tests that need a production configuration."""

from typing import Any

from pydantic import SecretStr

from app.core.config import Settings


def production_settings(**overrides: Any) -> Settings:
    """A valid production configuration (proxy authentication, https, real host)."""
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "production",
        "database_url": SecretStr("postgresql+asyncpg://careerpilot:x@db:5432/careerpilot"),
        "auth_mode": "proxy",
        "auth_proxy_secret": SecretStr("p" * 40),
        "cors_origins": "https://careerpilot.example.com",
        "allowed_hosts": "careerpilot.example.com,localhost",  # localhost: the test client
        "discovery_providers": "",
    }
    return Settings(**(values | overrides))


def unvalidated(settings: Settings, **changes: Any) -> Settings:
    """``settings`` with ``changes`` applied without validation: to test that code paths
    refuse unsafe values even if configuration validation were bypassed."""
    return settings.model_copy(update=changes)
