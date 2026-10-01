"""Applications, their status history, approvals, audit log, interviews, and follow-ups.

Human approval is enforced by the database, not only by application code: an application
cannot reach ``submitted`` or any later stage (assessment, interview, offer) without
``approved_at`` (the candidate's explicit approval) and ``submitted_at``, and an approved
application always records who approved which content (``approved_by_id``,
``approved_content_hash``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
from app.documents.models import CoverLetter, TailoredResume
from app.jobs.models import Job


class ApplicationStatus(StrEnum):
    """The application lifecycle, in order."""

    DISCOVERED = "discovered"  # found (e.g. through job discovery)
    SAVED = "saved"  # kept to consider
    ANALYZED = "analyzed"  # requirements analyzed and matched
    APPLICATION_PREPARED = "application_prepared"  # resume, cover letter, answers ready
    AWAITING_APPROVAL = "awaiting_approval"  # waiting for the candidate's explicit approval
    SUBMITTED = "submitted"  # the candidate submitted it (CareerPilot never submits)
    ASSESSMENT = "assessment"  # a test or take-home
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


# Statuses before submission, in lifecycle order.
PRE_SUBMISSION: tuple[ApplicationStatus, ...] = (
    ApplicationStatus.DISCOVERED,
    ApplicationStatus.SAVED,
    ApplicationStatus.ANALYZED,
    ApplicationStatus.APPLICATION_PREPARED,
    ApplicationStatus.AWAITING_APPROVAL,
)
# Statuses only reachable after the candidate explicitly approved AND submitted it.
APPROVAL_REQUIRED_STATUSES: tuple[ApplicationStatus, ...] = (
    ApplicationStatus.SUBMITTED,
    ApplicationStatus.ASSESSMENT,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
)
_APPROVAL_REQUIRED_SQL = ", ".join(f"'{s.value}'" for s in APPROVAL_REQUIRED_STATUSES)


class ApprovalState(StrEnum):
    """Human-in-the-loop approval, separate from the lifecycle status."""

    DRAFT = "draft"  # being prepared
    READY_FOR_REVIEW = "ready_for_review"  # the candidate asked to review it
    APPROVED = "approved"  # the candidate explicitly approved one specific content version
    REJECTED = "rejected"  # the candidate rejected it (or withdrew an approval)
    SUBMITTED = "submitted"  # submitted after approval


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


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
        CheckConstraint(
            f"status NOT IN ({_APPROVAL_REQUIRED_SQL}) OR submitted_at IS NOT NULL",
            "submitted_has_time",
        ),
        Index("ix_applications_profile_status", "candidate_profile_id", "status"),
        # An approval always records who approved which content.
        CheckConstraint(
            "approval_state <> 'approved' OR (approved_at IS NOT NULL "
            "AND approved_by_id IS NOT NULL AND approved_content_hash IS NOT NULL)",
            "approval_recorded",
        ),
        # Not approved (yet, or any more): nothing approved, nothing submitted.
        CheckConstraint(
            "approval_state NOT IN ('draft', 'ready_for_review', 'rejected') "
            "OR (approved_at IS NULL AND submitted_at IS NULL)",
            "unapproved_has_no_approval",
        ),
        CheckConstraint(
            "(approval_state = 'submitted') = (submitted_at IS NOT NULL)",
            "submitted_state_matches",
        ),
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
        ApplicationStatus, default=ApplicationStatus.SAVED, server_default="saved"
    )
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_state: Mapped[ApprovalState] = enum_column(
        ApprovalState, default=ApprovalState.DRAFT, server_default="draft"
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[uuid.UUID | None] = fk_column("users.id", nullable=True)
    approved_content_hash: Mapped[str | None] = mapped_column(String(64))
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


class ApplicationApproval(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only: every approval or rejection, with the exact content that was reviewed."""

    __tablename__ = "application_approvals"
    __table_args__ = (UniqueConstraint("application_id", "version"),)

    application_id: Mapped[uuid.UUID] = fk_column("applications.id", index=False)
    reviewer_id: Mapped[uuid.UUID] = fk_column("users.id")
    decision: Mapped[ApprovalDecision] = enum_column(ApprovalDecision)
    version: Mapped[int] = mapped_column(Integer)  # 1, 2, ... per application
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the reviewed content snapshot
    note: Mapped[str | None] = mapped_column(Text)


class ApplicationAuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only: review, approval and submission events (and refused attempts)."""

    __tablename__ = "application_audit_events"
    __table_args__ = (
        Index("ix_application_audit_events_app_created", "application_id", "created_at"),
    )

    application_id: Mapped[uuid.UUID] = fk_column("applications.id", index=False)
    actor: Mapped[StatusActor] = enum_column(StatusActor)
    user_id: Mapped[uuid.UUID | None] = fk_column("users.id", nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
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
