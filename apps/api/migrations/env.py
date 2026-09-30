"""Alembic environment: runs migrations over the app's async engine.

Callers (e.g. the test suite) may pass an open sync connection via
``config.attributes["connection"]`` to migrate a different database.
"""

import asyncio
from logging.config import fileConfig
from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from pgvector.sqlalchemy import Vector
from sqlalchemy.engine import Connection

from app.db.models import Base
from app.db.session import dispose_engine, get_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render pgvector columns as ``Vector(dim)`` with the import autogenerate misses."""
    if type_ == "type" and isinstance(obj, Vector):
        autogen_context.imports.add("from pgvector.sqlalchemy import Vector")
        return f"Vector({obj.dim})"
    return False


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    try:
        async with get_engine().connect() as connection:
            await connection.run_sync(_run)
            await connection.commit()
    finally:
        await dispose_engine()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; set DATABASE_URL and run online.")

external_connection = config.attributes.get("connection")
if external_connection is not None:
    _run(external_connection)
else:
    asyncio.run(_run_async())
