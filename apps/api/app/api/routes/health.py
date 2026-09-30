"""Health endpoints.

- ``GET /health``        liveness: the process is up. Never touches the database.
- ``GET /health/ready``  readiness: the database is reachable and pgvector is installed.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.db.session import DatabaseStatus, check_database

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str


class DatabaseCheck(BaseModel):
    connected: bool
    pgvector: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DatabaseCheck


async def database_status() -> DatabaseStatus:
    """Dependency wrapper so tests can override the database probe."""
    return await check_database()


@router.get("", response_model=HealthResponse)
async def liveness(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.app_env)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(
    response: Response, db: DatabaseStatus = Depends(database_status)
) -> ReadinessResponse:
    ready = db.connected and db.pgvector
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        database=DatabaseCheck(connected=db.connected, pgvector=db.pgvector, detail=db.detail),
    )
