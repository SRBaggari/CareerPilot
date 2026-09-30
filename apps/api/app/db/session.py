"""Async engine and session management.

The engine is created lazily so the application (and its tests) can start without a
database configured. Call ``dispose_engine()`` on shutdown.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when a database operation is attempted without DATABASE_URL set."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    return _init()[0]


def _init() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    global _engine, _sessionmaker
    if _engine is None or _sessionmaker is None:
        settings = get_settings()
        if settings.database_url is None:
            raise DatabaseNotConfiguredError("DATABASE_URL is not set")
        _engine = create_async_engine(
            settings.database_url.get_secret_value(),
            echo=settings.database_echo,
            pool_pre_ping=True,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine, _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session bound to the shared engine."""
    _, sessionmaker = _init()
    async with sessionmaker() as session:
        yield session


@dataclass(frozen=True)
class DatabaseStatus:
    connected: bool
    pgvector: bool
    detail: str | None = None


async def check_database() -> DatabaseStatus:
    """Verify connectivity and that the pgvector extension is installed."""
    try:
        engine = get_engine()
    except DatabaseNotConfiguredError:
        return DatabaseStatus(connected=False, pgvector=False, detail="not configured")

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            has_vector = await conn.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            )
    except Exception as exc:  # report, never crash the health check
        return DatabaseStatus(connected=False, pgvector=False, detail=type(exc).__name__)

    return DatabaseStatus(
        connected=True,
        pgvector=bool(has_vector),
        detail=None if has_vector else "pgvector extension not installed; run migrations",
    )
