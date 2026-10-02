"""Application settings, loaded from environment variables (and an optional .env file).

Secrets are typed as ``SecretStr`` so they are never printed in logs or reprs.
"""

import re
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Configuration errors must never echo values: they can include passwords and keys.
        hide_input_in_errors=True,
    )

    app_name: str = "CareerPilot API"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    # "json": one JSON object per line (for log collectors); "text": readable lines.
    log_format: Literal["text", "json"] = "text"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    # Host headers the API answers to (blocks DNS-rebinding attacks on a local install).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"]
    )
    max_request_bytes: int = Field(default=10 * 1024 * 1024, gt=0)  # whole request body

    # Optional so the API can boot (and report "not configured") without a database.
    database_url: SecretStr | None = None
    database_echo: bool = False

    # Who is making a request:
    # - "dev": every request from this computer acts as DEV_USER_EMAIL (development only).
    # - "proxy": an authenticating reverse proxy in front of the API (e.g. Caddy with basic
    #   auth, or oauth2-proxy) sends the signed-in user's email in AUTH_PROXY_USER_HEADER,
    #   plus AUTH_PROXY_SECRET in AUTH_PROXY_SECRET_HEADER to prove the request came
    #   through it. Required in production.
    auth_mode: Literal["dev", "proxy"] = "dev"
    dev_user_email: str | None = None
    auth_proxy_user_header: str = "X-CareerPilot-User"
    auth_proxy_secret_header: str = "X-CareerPilot-Proxy-Secret"  # noqa: S105 - a header name
    auth_proxy_secret: SecretStr | None = None

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

    @field_validator("cors_origins", "discovery_providers", "allowed_hosts", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _exact_origins(cls, value: list[str]) -> list[str]:
        """Only exact http(s) origins: a wildcard with credentials would trust every site."""
        for origin in value:
            if not re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+", origin):
                raise ValueError(f"CORS origin must be an exact http(s) origin: {origin!r}")
        return value

    @model_validator(mode="after")
    def _safe_in_production(self) -> "Settings":
        """Refuse to start in production with an unsafe or incomplete configuration."""
        if self.app_env != "production":
            return self
        problems = production_problems(self)
        if problems:
            raise ValueError("Unsafe production configuration: " + " ".join(problems))
        return self

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the 'postgresql+asyncpg://' scheme")
        return value


def production_problems(settings: Settings) -> list[str]:
    """What stops ``settings`` from being safe to run in production (empty when fine)."""
    problems: list[str] = []
    if settings.database_url is None:
        problems.append("DATABASE_URL is required.")
    if settings.database_echo:
        problems.append("DATABASE_ECHO would log personal data; turn it off.")
    if settings.auth_mode != "proxy":
        problems.append("AUTH_MODE must be 'proxy' (an authenticating reverse proxy).")
    secret = settings.auth_proxy_secret.get_secret_value() if settings.auth_proxy_secret else ""
    if settings.auth_mode == "proxy" and len(secret) < 32:
        problems.append("AUTH_PROXY_SECRET must be at least 32 random characters.")
    insecure = [o for o in settings.cors_origins if not o.startswith("https://")]
    if insecure:
        problems.append(f"CORS_ORIGINS must use https: {', '.join(insecure)}.")
    if set(settings.allowed_hosts) <= {"localhost", "127.0.0.1", "[::1]"}:
        problems.append("ALLOWED_HOSTS must list the public host name(s).")
    if settings.automation_mock_site_url:
        problems.append("AUTOMATION_MOCK_SITE_URL is for development only; unset it.")
    if "mock" in settings.discovery_providers:
        problems.append("DISCOVERY_PROVIDERS must not include 'mock' (sample data).")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
