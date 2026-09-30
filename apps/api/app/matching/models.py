"""Candidate-job match scores and the skill gaps behind them."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column, fk_column
from app.jobs.models import Job, JobRequirement
from app.profiles.models import Skill


class GapSeverity(StrEnum):
    BLOCKING = "blocking"  # an unmet required qualification
    SIGNIFICANT = "significant"
    MINOR = "minor"


def _unit_interval(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} >= 0 AND {column} <= 1", f"{column}_range")


class JobMatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Latest match result for one candidate and one job (recomputed in place)."""

    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", "job_id"),
        _unit_interval("overall_score"),
        _unit_interval("semantic_score"),
        _unit_interval("skill_score"),
        Index("ix_job_matches_profile_score", "candidate_profile_id", "overall_score"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    job_id: Mapped[uuid.UUID] = fk_column("jobs.id")
    overall_score: Mapped[float] = mapped_column(Float)
    semantic_score: Mapped[float | None] = mapped_column(Float)
    skill_score: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(Text)
    # Identifies the scoring algorithm/weights so scores from different versions aren't mixed.
    scoring_version: Mapped[str] = mapped_column(String(50))

    job: Mapped[Job] = relationship()
    skill_gaps: Mapped[list[SkillGap]] = relationship(
        back_populates="job_match", cascade="all, delete-orphan", passive_deletes=True
    )


class SkillGap(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A job requirement the candidate's evidence does not (fully) satisfy."""

    __tablename__ = "skill_gaps"
    __table_args__ = (UniqueConstraint("job_match_id", "job_requirement_id"),)

    job_match_id: Mapped[uuid.UUID] = fk_column("job_matches.id", index=False)
    job_requirement_id: Mapped[uuid.UUID] = fk_column("job_requirements.id")
    skill_id: Mapped[uuid.UUID | None] = fk_column("skills.id", ondelete="SET NULL", nullable=True)
    severity: Mapped[GapSeverity] = enum_column(GapSeverity)
    description: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)

    job_match: Mapped[JobMatch] = relationship(back_populates="skill_gaps")
    job_requirement: Mapped[JobRequirement] = relationship()
    skill: Mapped[Skill | None] = relationship()
