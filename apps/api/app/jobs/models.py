"""Job postings and the structured requirements extracted from them.

Jobs are shared across users (deduplicated by source + external ID); everything personal
about a job (matches, documents, applications) lives in per-candidate tables.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column, fk_column
from app.db.vector import EmbeddingMixin, hnsw_cosine_index
from app.profiles.models import EmploymentType, Skill, WorkplaceType


class JobSource(StrEnum):
    MANUAL = "manual"  # pasted or entered by the user
    JOB_BOARD_API = "job_board_api"
    ATS_API = "ats_api"  # e.g. public Greenhouse/Lever job board APIs
    COMPANY_SITE = "company_site"
    FEED = "feed"
    OTHER = "other"


class RequirementType(StrEnum):
    SKILL = "skill"  # a skill statement ("Strong written communication")
    TECHNOLOGY = "technology"  # one named technology, linked to the skill vocabulary
    EXPERIENCE = "experience"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    ELIGIBILITY = "eligibility"  # work authorization, graduation year, location, ...
    RESPONSIBILITY = "responsibility"
    OTHER = "other"


class RequirementImportance(StrEnum):
    """How the job description itself qualifies a statement. Never inferred."""

    REQUIRED = "required"  # stated as required / must / minimum, or under a requirements heading
    PREFERRED = "preferred"  # nice to have / a plus / preferred
    INFORMATIONAL = "informational"  # describes the job (duties, stack) - not a requirement


class SalaryPeriod(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class JobInputMethod(StrEnum):
    PASTED_TEXT = "pasted_text"  # description pasted by the user and analyzed
    MANUAL_ENTRY = "manual_entry"  # fields entered by the user, no extraction
    DISCOVERED = "discovered"  # imported from a job source provider, then analyzed


class Job(UUIDPrimaryKeyMixin, TimestampMixin, EmbeddingMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # A posting is imported once per user. NULL external_id (manual entries) never
        # conflicts: NULLs are distinct.
        UniqueConstraint("created_by_user_id", "source", "source_name", "external_id"),
        CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_max >= salary_min",
            "salary_range",
        ),
        CheckConstraint("salary_currency ~ '^[A-Z]{3}$'", "salary_currency_iso"),
        Index("ix_jobs_active_posted", "is_active", "posted_at"),
        hnsw_cosine_index("jobs"),
    )

    source: Mapped[JobSource] = enum_column(JobSource)
    source_name: Mapped[str | None] = mapped_column(String(100))  # e.g. "greenhouse"
    external_id: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300))
    company_name: Mapped[str] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(300))
    workplace_type: Mapped[WorkplaceType | None] = enum_column(WorkplaceType)
    employment_type: Mapped[EmploymentType | None] = enum_column(EmploymentType)
    seniority: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[SalaryPeriod | None] = enum_column(SalaryPeriod)
    salary_text: Mapped[str | None] = mapped_column(Text)  # verbatim, when explicitly stated
    application_deadline: Mapped[date | None] = mapped_column(Date)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # When the user adds a job manually. Kept if the user is deleted.
    created_by_user_id: Mapped[uuid.UUID | None] = fk_column(
        "users.id", ondelete="SET NULL", nullable=True
    )
    # Analysis provenance (user-added jobs).
    input_method: Mapped[JobInputMethod | None] = enum_column(JobInputMethod)
    analyzer_name: Mapped[str | None] = mapped_column(String(50))  # "heuristic", "llm:<model>"
    analysis_warnings: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requirements: Mapped[list[JobRequirement]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="JobRequirement.sort_order",
    )


class JobRequirement(UUIDPrimaryKeyMixin, TimestampMixin, EmbeddingMixin, Base):
    __tablename__ = "job_requirements"
    __table_args__ = (
        CheckConstraint("min_years >= 0", "min_years_non_negative"),
        CheckConstraint("length(btrim(description)) > 0", "description_not_blank"),
        hnsw_cosine_index("job_requirements"),
    )

    job_id: Mapped[uuid.UUID] = fk_column("jobs.id")
    requirement_type: Mapped[RequirementType] = enum_column(RequirementType)
    importance: Mapped[RequirementImportance] = enum_column(RequirementImportance)
    description: Mapped[str] = mapped_column(Text)
    # Normalized link to the skill vocabulary when the requirement is a specific skill.
    skill_id: Mapped[uuid.UUID | None] = fk_column("skills.id", ondelete="SET NULL", nullable=True)
    min_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    # The verbatim job-description text this requirement comes from.
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    job: Mapped[Job] = relationship(back_populates="requirements")
    skill: Mapped[Skill | None] = relationship()
