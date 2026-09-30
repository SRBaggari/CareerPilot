"""Declarative base and column helpers shared by all ORM models.

Model modules are imported in ``app.db.models`` so Alembic autogenerate can see them.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    MetaData,
    Table,
    Uuid,
    event,
    func,
    text,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, mapped_column

# Deterministic constraint names keep Alembic migrations stable across environments.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Enum columns are VARCHAR + CHECK rather than native PostgreSQL enums, so adding a value
# is a plain constraint change. Fixed length leaves headroom for future values.
ENUM_LENGTH = 32


class Base(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def enum_column(enum_cls: type[StrEnum], **kwargs: Any) -> MappedColumn[Any]:
    """VARCHAR column restricted to ``enum_cls`` values by a CHECK constraint named
    ``ck_<table>_<column>_enum``.

    The CHECK is attached explicitly (rather than via ``Enum(create_constraint=True)``) so
    Alembic autogenerate renders it exactly once, under the naming convention.
    """
    column = mapped_column(
        Enum(
            enum_cls,
            native_enum=False,
            create_constraint=False,
            length=ENUM_LENGTH,
            values_callable=lambda cls: [member.value for member in cls],
            validate_strings=True,
        ),
        **kwargs,
    )
    allowed = ", ".join(f"'{member.value}'" for member in enum_cls)

    @event.listens_for(column.column, "after_parent_attach")
    def _add_check(col: Column[Any], table: Table) -> None:
        table.append_constraint(CheckConstraint(f"{col.name} IN ({allowed})", f"{col.name}_enum"))

    return column


def fk_column(
    target: str,
    *,
    ondelete: str | None = "CASCADE",
    nullable: bool = False,
    index: bool = True,
) -> MappedColumn[Any]:
    """Foreign-key UUID column, indexed by default (PostgreSQL does not index FKs itself).

    ``ondelete=None`` means NO ACTION: deleting the referenced row fails while it is still
    referenced, but rows removed by the same cascading statement are allowed.
    """
    return mapped_column(ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)
