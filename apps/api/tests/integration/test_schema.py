"""The migrated database matches the ORM models and has the expected structure."""

from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base

pytestmark = pytest.mark.anyio


async def test_migrations_match_models(db: AsyncSession) -> None:
    def diff(sync_conn: Connection) -> list[Any]:
        ctx = MigrationContext.configure(
            sync_conn, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(ctx, Base.metadata))

    conn = await db.connection()
    assert await conn.run_sync(diff) == []


async def test_all_model_tables_exist(db: AsyncSession) -> None:
    rows = await db.scalars(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
        )
    )
    assert set(rows) == set(Base.metadata.tables)


async def test_every_foreign_key_is_indexed(db: AsyncSession) -> None:
    unindexed = await db.execute(
        text(
            """
            SELECT c.conrelid::regclass::text, c.conname
            FROM pg_constraint c
            WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
              AND NOT EXISTS (
                SELECT 1 FROM pg_index i
                WHERE i.indrelid = c.conrelid
                  AND (i.indkey::int2[])[0:array_length(c.conkey, 1) - 1] = c.conkey::int2[]
              )
            """
        )
    )
    assert unindexed.all() == []


async def test_embedding_columns_have_hnsw_indexes(db: AsyncSession) -> None:
    rows = await db.scalars(
        text("SELECT tablename FROM pg_indexes WHERE indexdef ILIKE '%USING hnsw%'")
    )
    assert set(rows) == {"candidate_evidence", "jobs", "job_requirements"}


async def test_claim_evidence_fk_is_deferred_no_action(db: AsyncSession) -> None:
    # Alembic autogenerate does not compare deferrability, so assert it on the live schema.
    row = (
        await db.execute(
            text(
                "SELECT confdeltype::text, condeferrable, condeferred FROM pg_constraint "
                "WHERE conname = 'fk_generated_claim_evidence_evidence_id_candidate_evidence'"
            )
        )
    ).one()
    assert tuple(row) == ("a", True, True)  # NO ACTION, DEFERRABLE INITIALLY DEFERRED


async def test_check_constraints_match_models(db: AsyncSession) -> None:
    # Autogenerate does not detect CHECK constraints, so compare names with the models.
    from sqlalchemy import CheckConstraint

    expected = {
        str(c.name)
        for table in Base.metadata.tables.values()
        for c in table.constraints
        if isinstance(c, CheckConstraint)
    }
    actual = set(
        await db.scalars(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
            )
        )
    )
    assert actual == expected


async def test_enum_checks_allow_exactly_the_model_values(db: AsyncSession) -> None:
    """Catches migrations that forget to update a CHECK when enum values change."""
    import re
    from enum import StrEnum

    from sqlalchemy import Enum

    definitions: dict[str, str] = dict(
        (
            await db.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    r"WHERE contype = 'c' AND conname LIKE '%\_enum'"
                )
            )
        ).all()
    )
    for table in Base.metadata.tables.values():
        for column in table.c:
            if not isinstance(column.type, Enum) or column.type.enum_class is None:
                continue
            enum_cls = column.type.enum_class
            definition = definitions[f"ck_{table.name}_{column.name}_enum"]
            allowed = set(re.findall(r"'([a-z_]+)'", definition))
            assert issubclass(enum_cls, StrEnum)
            assert allowed == {m.value for m in enum_cls}, (table.name, column.name)
