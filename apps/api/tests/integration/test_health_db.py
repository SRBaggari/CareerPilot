import pytest

from app.core.config import get_settings
from app.db.session import check_database, dispose_engine

pytestmark = pytest.mark.anyio


async def test_readiness_probe_sees_real_database_and_pgvector(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        status = await check_database()
    finally:
        await dispose_engine()
        get_settings.cache_clear()
    assert status.connected, status.detail
    assert status.pgvector
