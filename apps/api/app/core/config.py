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
    # Job description analysis: same choice as the resume parser.
    job_analyzer: Literal["auto", "heuristic", "llm"] = "auto"
    # Candidate-job matching judge: rules only, or an LLM grounded against retrieved evidence.
    match_judge: Literal["auto", "rules", "llm"] = "auto"
    # Tailored resumes: rules only, or LLM wording. Every claim is verified either way.
    resume_generator: Literal["auto", "rules", "llm"] = "auto"
    # Claim verification: rule checks only, or rules plus an LLM reviewer. The reviewer can
    # make any verdict stricter, but can't overrule a hard factual failure.
    claim_verifier: Literal["auto", "rules", "llm"] = "auto"
    # Cover letters: rule-based letter, or LLM wording. Every sentence is verified either way.
    cover_letter_generator: Literal["auto", "rules", "llm"] = "auto"
    # Application answers: rule-based or LLM wording. Every sentence is verified either way.
    answer_generator: Literal["auto", "rules", "llm"] = "auto"
    # Job discovery: provider adapters to enable, by name (comma-separated). Only sources
    # that permit automated access can have adapters; "mock" is development sample data.
    discovery_providers: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["mock"])

    # Browser-assisted applications (Playwright). CareerPilot fills forms only on supported
    # sites and never submits without the candidate's explicit confirmation.
    automation_headless: bool = True
    automation_timeout_ms: int = Field(default=15000, gt=0)
    # The local mock application site (development and tests only; disabled in production).
    automation_mock_site_url: str | None = None

    @field_validator("cors_origins", "discovery_providers", mode="before")
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
