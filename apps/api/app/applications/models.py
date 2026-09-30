"""Applications, their status history, interviews, and follow-ups.

Human approval is enforced by the database, not only by application code: an application
cannot reach any status at or beyond ``approved`` without ``approved_at``, and cannot be
``submitted`` without ``submitted_at``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    fk_column,
)
from app.documents.models import CoverLetter, TailoredResume
from app.jobs.models import Job


class ApplicationStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"  # candidate explicitly approved; automation may start
    FILLING = "filling"  # browser automation filling the form
    AWAITING_SUBMISSION = "awaiting_submission"  # stopped before submit; candidate submits
    SUBMITTED = "submitted"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


# Statuses that are only reachable after explicit human approval.
APPROVAL_REQUIRED_STATUSES: tuple[ApplicationStatus, ...] = (
    ApplicationStatus.APPROVED,
    ApplicationStatus.FILLING,
    ApplicationStatus.AWAITING_SUBMISSION,
    ApplicationStatus.SUBMITTED,
    ApplicationStatus.INTERVIEWING,
    ApplicationStatus.OFFER,
    ApplicationStatus.ACCEPTED,
    ApplicationStatus.REJECTED,
)
_APPROVAL_REQUIRED_SQL = ", ".join(f"'{s.value}'" for s in APPROVAL_REQUIRED_STATUSES)


class StatusActor(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AUTOMATION = "automation"


class InterviewType(StrEnum):
    PHONE_SCREEN = "phone_screen"
    TECHNICAL = "technical"
    BEHAVIORAL = "behavioral"
    TAKE_HOME = "take_home"
    ONSITE = "onsite"
    PANEL = "panel"
    OTHER = "other"


class InterviewStatus(StrEnum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class FollowUpChannel(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    LINKEDIN = "linkedin"
    OTHER = "other"


class FollowUpStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


class Application(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", "job_id"),
        CheckConstraint(
            f"status NOT IN ({_APPROVAL_REQUIRED_SQL}) OR approved_at IS NOT NULL",
            "approval_required",
        ),
        CheckConstraint(
            "submitted_at IS NULL OR (approved_at IS NOT NULL AND submitted_at >= approved_at)",
            "submitted_after_approval",
        ),
        CheckConstraint("status <> 'submitted' OR submitted_at IS NOT NULL", "submitted_has_time"),
        Index("ix_applications_profile_status", "candidate_profile_id", "status"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    job_id: Mapped[uuid.UUID] = fk_column("jobs.id", ondelete=None)
    # NO ACTION: documents attached to an application can't be deleted out from under it.
    tailored_resume_id: Mapped[uuid.UUID | None] = fk_column(
        "tailored_resumes.id", ondelete=None, nullable=True
    )
    cover_letter_id: Mapped[uuid.UUID | None] = fk_column(
        "cover_letters.id", ondelete=None, nullable=True
    )
    status: Mapped[ApplicationStatus] = enum_column(
        ApplicationStatus, default=ApplicationStatus.DRAFT, server_default="draft"
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    application_url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship()
    tailored_resume: Mapped[TailoredResume | None] = relationship()
    cover_letter: Mapped[CoverLetter | None] = relationship()
    status_history: Mapped[list[ApplicationStatusHistory]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApplicationStatusHistory.created_at",
    )
    interviews: Mapped[list[Interview]] = relationship(
        back_populates="application", cascade="all, delete-orphan", passive_deletes=True
    )
    follow_ups: Mapped[list[FollowUp]] = relationship(
        back_populates="application", cascade="all, delete-orphan", passive_deletes=True
    )


class ApplicationStatusHistory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only log of status transitions."""

    __tablename__ = "application_status_history"
    __table_args__ = (
        Index("ix_application_status_history_app_created", "application_id", "created_at"),
    )

    application_id: Mapped[uuid.UUID] = fk_column("applications.id", index=False)
    from_status: Mapped[ApplicationStatus | None] = enum_column(ApplicationStatus)
    to_status: Mapped[ApplicationStatus] = enum_column(ApplicationStatus)
    actor: Mapped[StatusActor] = enum_column(StatusActor)
    note: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="status_history")


class Interview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "interviews"
    __table_args__ = (CheckConstraint("duration_minutes > 0", "duration_positive"),)

    application_id: Mapped[uuid.UUID] = fk_column("applications.id")
    interview_type: Mapped[InterviewType] = enum_column(InterviewType)
    status: Mapped[InterviewStatus] = enum_column(
        InterviewStatus, default=InterviewStatus.SCHEDULED, server_default="scheduled"
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(300))
    meeting_url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="interviews")


class FollowUp(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A follow-up the candidate intends to make. CareerPilot never sends these itself."""

    __tablename__ = "follow_ups"
    __table_args__ = (
        CheckConstraint("status <> 'done' OR completed_at IS NOT NULL", "done_has_timestamp"),
        Index("ix_follow_ups_status_due", "status", "due_at"),
    )

    application_id: Mapped[uuid.UUID] = fk_column("applications.id")
    interview_id: Mapped[uuid.UUID | None] = fk_column(
        "interviews.id", ondelete="SET NULL", nullable=True
    )
    channel: Mapped[FollowUpChannel] = enum_column(FollowUpChannel)
    status: Mapped[FollowUpStatus] = enum_column(
        FollowUpStatus, default=FollowUpStatus.PENDING, server_default="pending"
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="follow_ups")
