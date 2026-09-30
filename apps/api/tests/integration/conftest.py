"""Integration-test fixtures. Require TEST_DATABASE_URL (a disposable database: the schema is
dropped and re-created once per session). Every test runs inside a transaction that is
rolled back, so nothing a test writes is persisted.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes.resumes import get_resume_llm
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.main import create_app
from app.users.dependencies import get_current_user
from app.users.models import User

API_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.fspath)).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.integration)
            if not TEST_DATABASE_URL:
                item.add_marker(pytest.mark.skip(reason="TEST_DATABASE_URL not set"))


def alembic_config(connection: Connection) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.attributes["connection"] = connection
    cfg.attributes["configure_logger"] = False
    return cfg


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: command.downgrade(alembic_config(c), "base"))
            await conn.run_sync(lambda c: command.upgrade(alembic_config(c), "head"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    assert TEST_DATABASE_URL is not None
    asyncio.run(_reset_schema(TEST_DATABASE_URL))
    return TEST_DATABASE_URL


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def make_user(db: AsyncSession, email: str) -> User:
    user = User(email=email)
    db.add(user)
    await db.flush()
    return user


@pytest.fixture
async def user(db: AsyncSession) -> User:
    return await make_user(db, "candidate@example.test")


def client_for(
    db: AsyncSession,
    user: User,
    *,
    settings: Settings | None = None,
    overrides: dict[Callable[..., Any], Callable[..., Any]] | None = None,
) -> httpx2.AsyncClient:
    """An HTTP client for the real app, acting as ``user`` inside the test transaction.

    Settings never come from the developer's environment, and no real AI provider is used
    unless a test overrides ``get_resume_llm`` itself.
    """
    settings = settings or Settings(_env_file=None, app_env="test")
    app = create_app(settings)

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db

    user_id = user.id  # resolve per request, as production does (survives rollbacks)

    async def _user() -> User | None:
        return await db.get(User, user_id)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_resume_llm] = lambda: None
    app.dependency_overrides.update(overrides or {})
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(db: AsyncSession, user: User) -> AsyncIterator[httpx2.AsyncClient]:
    async with client_for(db, user) as http:
        yield http
