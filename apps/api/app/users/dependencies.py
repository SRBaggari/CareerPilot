"""Resolves the user making the request.

Real authentication (Auth.js session verification) arrives in a later phase. Until then,
development and test environments act as a single local user named by ``DEV_USER_EMAIL``;
production refuses every request. Callers depend only on ``get_current_user``, so swapping in
real auth changes nothing else.
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.users.models import User

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    if settings.app_env == "production" or not settings.dev_user_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is not configured",
        )
    # The development identity is only for the machine it runs on: a server started
    # without APP_ENV=production must not hand that identity to the network.
    client = request.client.host if request.client else None
    allowed = LOOPBACK | ({"testclient"} if settings.app_env == "test" else set())
    if client not in allowed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The development login only works from this computer",
        )
    email = settings.dev_user_email.strip().lower()
    await session.execute(insert(User).values(email=email).on_conflict_do_nothing())
    user = await session.scalar(select(User).where(User.email == email))
    await session.commit()
    if user is None:  # pragma: no cover - the insert above guarantees a row
        raise HTTPException(status_code=500, detail="Could not resolve development user")
    return user
