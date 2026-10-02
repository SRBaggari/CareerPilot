import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.config import Settings
from app.users.dependencies import get_current_user
from app.users.models import User

from ..settings_helpers import production_settings, unvalidated

pytestmark = pytest.mark.anyio


def _request(host: str = "127.0.0.1") -> Request:
    return Request({"type": "http", "client": (host, 50000), "headers": []})


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


async def test_dev_user_is_created_once_and_reused(db: AsyncSession) -> None:
    settings = _settings(app_env="development", dev_user_email=" Me@Localhost.Dev ")
    first = await get_current_user(_request(), db, settings)
    second = await get_current_user(_request(), db, settings)
    assert first.id == second.id
    assert first.email == "me@localhost.dev"
    assert await db.scalar(select(func.count()).select_from(User)) == 1


async def test_requests_are_rejected_without_an_identity(db: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request(), db, _settings(app_env="development"))
    assert exc.value.status_code == 401


async def test_the_development_login_never_works_in_production(db: AsyncSession) -> None:
    # Configuration refuses this combination; the login refuses it independently.
    settings = unvalidated(
        production_settings(), auth_mode="dev", dev_user_email="me@localhost.dev"
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request(), db, settings)
    assert exc.value.status_code == 401


async def test_the_development_login_only_works_from_this_computer(db: AsyncSession) -> None:
    settings = _settings(app_env="development", dev_user_email="me@localhost.dev")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request("192.168.1.20"), db, settings)
    assert exc.value.status_code == 401
    assert (await get_current_user(_request("::1"), db, settings)).email == "me@localhost.dev"


# --- Behind an authenticating proxy (production) ------------------------------------------

SECRET = "s" * 40


def _proxied(headers: dict[str, str], host: str = "10.0.0.5") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "client": (host, 50000), "headers": raw})


def _proxy_settings() -> Settings:
    from pydantic import SecretStr

    return _settings(app_env="development", auth_mode="proxy", auth_proxy_secret=SecretStr(SECRET))


async def test_the_proxy_identifies_the_user(db: AsyncSession) -> None:
    request = _proxied(
        {"X-CareerPilot-User": "Asha@Example.Test", "X-CareerPilot-Proxy-Secret": SECRET}
    )
    user = await get_current_user(request, db, _proxy_settings())
    assert user.email == "asha@example.test"  # from any address: the proxy vouches


@pytest.mark.parametrize(
    "headers",
    [
        {"X-CareerPilot-User": "asha@example.test"},  # not through the proxy
        {"X-CareerPilot-User": "asha@example.test", "X-CareerPilot-Proxy-Secret": "guess"},
        {"X-CareerPilot-Proxy-Secret": SECRET},  # no user
        {"X-CareerPilot-User": "not-an-email", "X-CareerPilot-Proxy-Secret": SECRET},
    ],
)
async def test_requests_not_vouched_for_by_the_proxy_are_refused(
    db: AsyncSession, headers: dict[str, str]
) -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_proxied(headers), db, _proxy_settings())
    assert exc.value.status_code == 401
    assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_proxy_mode_without_a_secret_refuses_everyone(db: AsyncSession) -> None:
    settings = _settings(app_env="development", auth_mode="proxy")
    with pytest.raises(HTTPException):
        await get_current_user(
            _proxied({"X-CareerPilot-User": "a@example.test", "X-CareerPilot-Proxy-Secret": ""}),
            db,
            settings,
        )
