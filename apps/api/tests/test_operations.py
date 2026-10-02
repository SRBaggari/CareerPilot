"""Production operations: request IDs, logging, error handling, configuration checks."""

import json
import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, production_problems
from app.core.logging import JsonFormatter
from app.main import create_app

from .settings_helpers import production_settings


def _client(**settings: object) -> TestClient:
    app = create_app(Settings(_env_file=None, app_env="test", **settings))  # type: ignore[arg-type]
    boom = APIRouter()

    @boom.get("/boom")
    async def _boom() -> None:
        raise RuntimeError("SELECT * FROM users WHERE email = 'asha@example.test'")

    app.include_router(boom)
    return TestClient(app, raise_server_exceptions=False)


def test_every_response_carries_a_request_id() -> None:
    client = _client()
    generated = client.get("/health").headers["x-request-id"]
    assert len(generated) == 32
    kept = client.get("/health", headers={"x-request-id": "lb-1234.abc"})
    assert kept.headers["x-request-id"] == "lb-1234.abc"
    junk = client.get("/health", headers={"x-request-id": "<script>"})
    assert junk.headers["x-request-id"] != "<script>"


def test_unexpected_errors_are_generic_and_traceable(caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    with caplog.at_level(logging.ERROR, logger="careerpilot.errors"):
        response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    rid = response.headers["x-request-id"]
    assert body["request_id"] == rid and rid in body["detail"]
    assert "SELECT" not in response.text and "asha" not in response.text  # details stay in logs
    [record] = [r for r in caplog.records if r.name == "careerpilot.errors"]
    assert record.exc_info is not None
    assert response.headers["x-content-type-options"] == "nosniff"  # still secured


def test_access_lines_never_include_query_strings(caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    with caplog.at_level(logging.INFO, logger="careerpilot.access"):
        client.get("/health?token=secret-value")
    [line] = [r for r in caplog.records if r.name == "careerpilot.access"]
    assert line.getMessage().startswith("GET /health 200")
    assert "secret-value" not in line.getMessage()


def test_json_logs_are_one_object_per_line() -> None:
    record = logging.LogRecord("careerpilot.access", logging.INFO, "", 0, "GET /x 200", (), None)
    record.request_id = "abc"
    record.status = 200
    entry = json.loads(JsonFormatter().format(record))
    assert entry["level"] == "INFO" and entry["request_id"] == "abc" and entry["status"] == 200
    assert entry["message"] == "GET /x 200" and entry["time"]


def test_a_valid_production_configuration_starts() -> None:
    settings = production_settings()
    assert production_problems(settings) == []
    assert create_app(settings).docs_url is None  # no interactive docs in production


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"database_url": None}, "DATABASE_URL is required"),
        ({"auth_mode": "dev", "dev_user_email": "me@example.test"}, "AUTH_MODE must be 'proxy'"),
        ({"auth_proxy_secret": SecretStr("short")}, "at least 32 random characters"),
        ({"cors_origins": "http://careerpilot.example.com"}, "must use https"),
        ({"allowed_hosts": "localhost"}, "ALLOWED_HOSTS must list"),
        ({"automation_mock_site_url": "http://127.0.0.1:8790"}, "development only"),
        ({"discovery_providers": "mock"}, "must not include 'mock'"),
        ({"database_echo": True}, "DATABASE_ECHO"),
    ],
)
def test_unsafe_production_configurations_are_refused(
    overrides: dict[str, object], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        production_settings(**overrides)


def test_every_setting_is_documented_in_the_env_templates() -> None:
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    fields = {name.upper() for name in Settings.model_fields} - {"APP_NAME"}
    for template in (root / ".env.example", root / "apps" / "api" / ".env.example"):
        text = template.read_text(encoding="utf-8")
        documented = set(re.findall(r"\b([A-Z][A-Z0-9_]{3,})=", text))
        assert fields <= documented, (template.name, sorted(fields - documented))
        assert not re.search(r"sk-ant-[A-Za-z0-9-]{10,}|\bpa-[A-Za-z0-9-]{16,}", text)


def test_configuration_errors_never_echo_secret_values() -> None:
    with pytest.raises(ValidationError) as exc:
        production_settings(
            auth_mode="dev",
            anthropic_api_key=SecretStr("sk-ant-api03-NEVER-PRINTED"),
            database_url=SecretStr("postgresql+asyncpg://u:hunter2-db-password@db/careerpilot"),
        )
    assert "NEVER-PRINTED" not in str(exc.value) and "hunter2" not in str(exc.value)
