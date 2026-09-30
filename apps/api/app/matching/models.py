"""Candidate-job match results: per-requirement assessments, the evidence behind them,
scores, and the skill gaps that follow.

A match is an **evidence coverage** assessment: how well the candidate's verified evidence
covers the job's stated requirements. It is not a prediction of hiring outcomes.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column, fk_column
from app.jobs.models import Job, JobRequirement
from app.profiles.models import CandidateEvidence, Skill


class GapSeverity(StrEnum):
    BLOCKING = "blocking"  # an unmet required qualification
    SIGNIFICANT = "significant"
    MINOR = "minor"


class MatchStatus(StrEnum):
    MATCHED = "matched"  # verified evidence clearly satisfies the requirement
    PARTIAL = "partial"  # related evidence, but incomplete (e.g. fewer years, not named)
    MISSING = "missing"  # the evidence shows nothing that satisfies it
    UNKNOWN = "unknown"  # can't be assessed from the profile (e.g. work authorization)


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
        _unit_interval("required_coverage"),
        _unit_interval("preferred_coverage"),
        Index("ix_job_matches_profile_score", "candidate_profile_id", "overall_score"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    job_id: Mapped[uuid.UUID] = fk_column("jobs.id")
    overall_score: Mapped[float] = mapped_column(Float)  # evidence coverage, weighted
    semantic_score: Mapped[float | None] = mapped_column(Float)
    skill_score: Mapped[float | None] = mapped_column(Float)
    required_coverage: Mapped[float | None] = mapped_column(Float)
    preferred_coverage: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(Text)
    # Identifies the scoring algorithm/weights so scores from different versions aren't mixed.
    scoring_version: Mapped[str] = mapped_column(String(50))
    matcher_name: Mapped[str | None] = mapped_column(String(50))  # "rules" or "llm:<model>"
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    # Hash of the profile inputs used (evidence, skills, education, experience); a different
    # current value means the profile changed and the match should be recomputed.
    inputs_fingerprint: Mapped[str | None] = mapped_column(String(64))
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    job: Mapped[Job] = relationship()
    skill_gaps: Mapped[list[SkillGap]] = relationship(
        back_populates="job_match", cascade="all, delete-orphan", passive_deletes=True
    )
    requirement_matches: Mapped[list[RequirementMatch]] = relationship(
        back_populates="job_match", cascade="all, delete-orphan", passive_deletes=True
    )


# Evidence cited by one requirement assessment. Deleting the evidence removes the link
# (the match then reports itself as stale and should be recomputed).
requirement_match_evidence = Table(
    "requirement_match_evidence",
    Base.metadata,
    Column(
        "requirement_match_id",
        ForeignKey("requirement_matches.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "evidence_id",
        ForeignKey("candidate_evidence.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
    Column("similarity", Float, nullable=True),
    Column("rank", Integer, nullable=False, server_default="0"),
)


class RequirementMatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The assessment of one job requirement against the candidate's verified evidence."""

    __tablename__ = "requirement_matches"
    __table_args__ = (
        UniqueConstraint("job_match_id", "job_requirement_id"),
        CheckConstraint(
            "semantic_similarity >= -1 AND semantic_similarity <= 1", "similarity_range"
        ),
    )

    job_match_id: Mapped[uuid.UUID] = fk_column("job_matches.id", index=False)
    job_requirement_id: Mapped[uuid.UUID] = fk_column("job_requirements.id")
    status: Mapped[MatchStatus] = enum_column(MatchStatus)
    semantic_similarity: Mapped[float | None] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)
    judge: Mapped[str] = mapped_column(String(50))  # which judge produced it
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )  # structured-check facts, e.g. {"required_years": 5, "candidate_years": 3.2}

    job_match: Mapped[JobMatch] = relationship(back_populates="requirement_matches")
    job_requirement: Mapped[JobRequirement] = relationship()
    evidence: Mapped[list[CandidateEvidence]] = relationship(
        secondary=requirement_match_evidence, order_by=requirement_match_evidence.c.rank
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
