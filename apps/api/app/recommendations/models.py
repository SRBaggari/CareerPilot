"""Stored recommendations: one row per (candidate, discovered posting)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column, fk_column


class RecommendationStatus(StrEnum):
    NEW = "new"  # shown in the list
    SAVED = "saved"  # the candidate saved it
    IGNORED = "ignored"  # the candidate doesn't want to see it (kept hidden across refreshes)


class JobRecommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "job_recommendations"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", "source", "source_identifier"),
        Index("ix_job_recommendations_profile_status", "candidate_profile_id", "status"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    source: Mapped[str] = mapped_column(String(100))  # the job source provider
    source_identifier: Mapped[str] = mapped_column(String(200))
    posting: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the normalized posting, as seen
    status: Mapped[RecommendationStatus] = enum_column(
        RecommendationStatus, default=RecommendationStatus.NEW, server_default="new"
    )
    # Eligibility: a posting with exclusion reasons is filtered out (and says why).
    eligible: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    exclusions: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    concerns: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Why it is recommended: reasons, matched and missing skills, requirement results,
    # relevant projects, preference fit (see schemas.Explanation).
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    required_coverage: Mapped[float | None] = mapped_column(Float)
    overall_coverage: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    inputs_fingerprint: Mapped[str] = mapped_column(String(64))  # the profile it was computed for
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set once the candidate analyzes, tailors, or starts an application for it.
    job_id: Mapped[uuid.UUID | None] = fk_column("jobs.id", ondelete="SET NULL", nullable=True)
