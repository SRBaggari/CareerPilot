"""Resolves the user making the request.

Two modes (``AUTH_MODE``):

- ``dev``: every request from this computer acts as ``DEV_USER_EMAIL``. Development only:
  requests from other machines are refused, and production refuses this mode entirely.
- ``proxy``: CareerPilot runs behind an authenticating reverse proxy (Caddy with basic
  auth, oauth2-proxy, Cloudflare Access...). The proxy signs the user in and forwards
  their email in ``AUTH_PROXY_USER_HEADER``, together with ``AUTH_PROXY_SECRET`` in
  ``AUTH_PROXY_SECRET_HEADER``. The secret proves the request came through the proxy, so
  a client that reaches the API directly can't claim to be anyone.

Callers depend only on ``get_current_user``, so adding first-party login later changes
nothing else.
"""

import hmac
import re

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.users.models import User

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}")


def _refuse(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _dev_email(request: Request, settings: Settings) -> str:
    if settings.app_env == "production" or not settings.dev_user_email:
        raise _refuse("Authentication is not configured")
    # The development identity is only for the machine it runs on: a server started
    # without APP_ENV=production must not hand that identity to the network.
    client = request.client.host if request.client else None
    allowed = LOOPBACK | ({"testclient"} if settings.app_env == "test" else set())
    if client not in allowed:
        raise _refuse("The development login only works from this computer")
    return settings.dev_user_email


def _proxy_email(request: Request, settings: Settings) -> str:
    expected = settings.auth_proxy_secret.get_secret_value() if settings.auth_proxy_secret else ""
    supplied = request.headers.get(settings.auth_proxy_secret_header, "")
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise _refuse("Sign in through CareerPilot's login")
    email = request.headers.get(settings.auth_proxy_user_header, "").strip()
    if not _EMAIL.fullmatch(email):
        raise _refuse("Sign in with an email address")
    return email


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    raw = (
        _proxy_email(request, settings)
        if settings.auth_mode == "proxy"
        else _dev_email(request, settings)
    )
    email = raw.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:  # first sign-in: create the account (once, even if requests race)
        await session.execute(insert(User).values(email=email).on_conflict_do_nothing())
        user = await session.scalar(select(User).where(User.email == email))
        await session.commit()
    if user is None:  # pragma: no cover - the insert above guarantees a row
        raise HTTPException(status_code=500, detail="Could not resolve the user")
    return user
