"""Browser-assisted application runs and their append-only audit log."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    fk_column,
)


class RunStatus(StrEnum):
    PREPARING = "preparing"
    NEEDS_INPUT = "needs_input"  # fields only the candidate can answer (listed in problems)
    AWAITING_REVIEW = "awaiting_review"  # filled, read back, paused: nothing submitted
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"  # after the candidate's explicit confirmation only
    STOPPED = "stopped"  # unsupported site, access control, or changed form: explained
    FAILED = "failed"  # an unexpected error (e.g. the site timed out)
    CANCELLED = "cancelled"


class AuditActor(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AUTOMATION = "automation"


class ApplicationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "application_runs"
    __table_args__ = (
        # Submission requires the candidate's confirmation of a specific review.
        CheckConstraint(
            "status <> 'submitted' OR (confirmed_at IS NOT NULL AND submitted_at IS NOT NULL "
            "AND review_hash IS NOT NULL)",
            "submitted_requires_confirmation",
        ),
        CheckConstraint(
            "confirmed_at IS NULL OR review_hash IS NOT NULL", "confirmation_needs_review"
        ),
    )

    application_id: Mapped[uuid.UUID] = fk_column("applications.id")
    status: Mapped[RunStatus] = enum_column(
        RunStatus, default=RunStatus.PREPARING, server_default="preparing"
    )
    destination_url: Mapped[str] = mapped_column(Text)
    adapter: Mapped[str | None] = mapped_column(String(50))
    # Values the candidate provided for fields CareerPilot can't fill from their records.
    inputs: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # exactly what will be sent
    review_hash: Mapped[str | None] = mapped_column(String(64))
    problems: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    stop_reason: Mapped[str | None] = mapped_column(Text)
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_reference: Mapped[str | None] = mapped_column(String(300))

    events: Mapped[list[AutomationAuditEvent]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AutomationAuditEvent.created_at",
    )


class AutomationAuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only: one row per action taken (or refused) during a run."""

    __tablename__ = "automation_audit_events"
    __table_args__ = (Index("ix_automation_audit_events_run_created", "run_id", "created_at"),)

    run_id: Mapped[uuid.UUID] = fk_column("application_runs.id", index=False)
    actor: Mapped[AuditActor] = enum_column(AuditActor)
    action: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    run: Mapped[ApplicationRun] = relationship(back_populates="events")
