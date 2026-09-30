"""Application settings, loaded from environment variables (and an optional .env file).

Secrets are typed as ``SecretStr`` so they are never printed in logs or reprs.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CareerPilot API"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # Optional so the API can boot (and report "not configured") without a database.
    database_url: SecretStr | None = None
    database_echo: bool = False

    # Until real authentication exists, development/test requests act as this user.
    # Ignored (and all requests rejected) when app_env is "production".
    dev_user_email: str | None = None

    # AI provider configuration.
    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5-5"
    anthropic_api_key: SecretStr | None = None
    # Embeddings for evidence retrieval. "hash" is a deterministic, offline, *lexical*
    # embedder (tests and keyless development); use "voyage" for real semantic search.
    embedding_provider: Literal["hash", "voyage"] = "hash"
    embedding_model: str | None = None  # provider default when unset
    voyage_api_key: SecretStr | None = None

    # Resume ingestion.
    storage_dir: str = "storage"  # local directory for uploaded files (git-ignored)
    max_resume_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    # "auto": use the LLM parser when an API key is configured, else the rule-based parser.
    resume_parser: Literal["auto", "heuristic", "llm"] = "auto"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the 'postgresql+asyncpg://' scheme")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
