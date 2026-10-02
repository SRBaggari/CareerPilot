"""FastAPI application factory and ASGI entrypoint (``app.main:app``)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import RequestContext, configure_logging
from app.core.security import BodySizeLimit, CrossSiteGuard, SecurityHeaders
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs are handy locally but should not be public in production.
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
    )
    # Innermost first: the last middleware added runs first.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.add_middleware(CrossSiteGuard, allowed_origins=settings.cors_origins)
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_request_bytes)
    hosts = [
        *settings.allowed_hosts,
        *(["test", "testserver"] if settings.app_env == "test" else []),
    ]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(RequestContext)  # request IDs, access log, unexpected errors
    app.add_middleware(SecurityHeaders)
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
