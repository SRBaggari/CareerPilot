from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.health import database_status
from app.db.session import DatabaseStatus


def _override_db(app: FastAPI, result: DatabaseStatus) -> None:
    async def fake() -> DatabaseStatus:
        return result

    app.dependency_overrides[database_status] = fake


def test_liveness_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "CareerPilot API", "environment": "test"}


def test_readiness_ready_when_db_and_pgvector_available(app: FastAPI, client: TestClient) -> None:
    _override_db(app, DatabaseStatus(connected=True, pgvector=True))
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": {"connected": True, "pgvector": True, "detail": None},
    }


def test_readiness_503_when_pgvector_missing(app: FastAPI, client: TestClient) -> None:
    _override_db(app, DatabaseStatus(connected=True, pgvector=False, detail="missing"))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_readiness_503_when_db_down(app: FastAPI, client: TestClient) -> None:
    _override_db(app, DatabaseStatus(connected=False, pgvector=False, detail="OSError"))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["database"]["connected"] is False


def test_cors_allows_configured_frontend_origin(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
