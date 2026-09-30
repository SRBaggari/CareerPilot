import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.users.dependencies import get_current_user
from app.users.models import User

pytestmark = pytest.mark.anyio


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


async def test_dev_user_is_created_once_and_reused(db: AsyncSession) -> None:
    settings = _settings(app_env="development", dev_user_email=" Me@Localhost.Dev ")
    first = await get_current_user(db, settings)
    second = await get_current_user(db, settings)
    assert first.id == second.id
    assert first.email == "me@localhost.dev"
    assert await db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"app_env": "production", "dev_user_email": "me@localhost.dev"},
        {"app_env": "development", "dev_user_email": None},
    ],
)
async def test_requests_are_rejected_without_an_identity(
    db: AsyncSession, overrides: dict[str, object]
) -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(db, _settings(**overrides))
    assert exc.value.status_code == 401
