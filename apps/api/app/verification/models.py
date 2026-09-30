"""Stored verification reports: one row per verification run over a generated document."""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, enum_column, fk_column


class VerificationTrigger(StrEnum):
    GENERATION = "generation"  # the final check before a generated document is stored
    EDIT = "edit"  # the candidate saved edits
    MANUAL = "manual"  # the candidate asked for a re-check (e.g. after profile changes)
    APPROVAL = "approval"  # the final check when the candidate approves the document


class VerificationOutcome(StrEnum):
    APPROVED = "approved"  # every claim is SUPPORTED
    REJECTED = "rejected"  # at least one claim is not


class VerificationReport(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only: re-verifying a document adds a report, it never rewrites an old one."""

    __tablename__ = "verification_reports"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(tailored_resume_id, cover_letter_id, application_answer_id) = 1",
            "exactly_one_document",
        ),
    )

    tailored_resume_id: Mapped[uuid.UUID | None] = fk_column("tailored_resumes.id", nullable=True)
    cover_letter_id: Mapped[uuid.UUID | None] = fk_column("cover_letters.id", nullable=True)
    application_answer_id: Mapped[uuid.UUID | None] = fk_column(
        "application_answers.id", nullable=True
    )
    trigger: Mapped[VerificationTrigger] = enum_column(VerificationTrigger)
    outcome: Mapped[VerificationOutcome] = enum_column(VerificationOutcome)
    verifier: Mapped[str] = mapped_column(String(100))  # "rules" or "rules+llm:<model>"
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)  # counts and per-claim results
    ai_execution_log_id: Mapped[uuid.UUID | None] = fk_column(
        "ai_execution_logs.id", ondelete="SET NULL", nullable=True
    )
