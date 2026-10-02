"""Health endpoints.

- ``GET /health``        liveness: the process is up. Never touches the database.
- ``GET /health/ready``  readiness: the database is reachable, pgvector is installed, the
                         schema is at the latest migration, and file storage is writable.
                         503 otherwise, so a load balancer holds traffic back.
"""

import tempfile
from pathlib import Path
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
    migration: str | None = None
    migrations_current: bool | None = None


class StorageCheck(BaseModel):
    writable: bool


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DatabaseCheck
    storage: StorageCheck


async def database_status() -> DatabaseStatus:
    """Dependency wrapper so tests can override the database probe."""
    return await check_database()


def storage_status(settings: Settings = Depends(get_settings)) -> StorageCheck:
    """Uploaded resumes are written here: the directory must exist and be writable."""
    root = Path(settings.storage_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".health-"):
            pass
    except OSError:
        return StorageCheck(writable=False)
    return StorageCheck(writable=True)


@router.api_route("", methods=["GET", "HEAD"], response_model=HealthResponse)
async def liveness(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.app_env)


@router.api_route(
    "/ready",
    methods=["GET", "HEAD"],
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(
    response: Response,
    db: DatabaseStatus = Depends(database_status),
    storage: StorageCheck = Depends(storage_status),
) -> ReadinessResponse:
    ready = db.connected and db.pgvector and db.migrations_current is not False and storage.writable
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        database=DatabaseCheck(
            connected=db.connected,
            pgvector=db.pgvector,
            detail=db.detail,
            migration=db.migration,
            migrations_current=db.migrations_current,
        ),
        storage=storage,
    )
