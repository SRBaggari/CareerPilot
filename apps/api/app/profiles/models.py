"""Candidate master profile and the evidence that backs every candidate claim.

``CandidateEvidence`` is the source of truth: each row is a concrete, candidate-provided
statement (e.g. "Implemented a RAG pipeline using document retrieval and question
answering") attached to the profile item it describes. Generated resume and cover-letter
claims cite evidence rows; they never stand on their own.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_array_column,
    enum_column,
    fk_column,
)
from app.db.vector import EmbeddingMixin, hnsw_cosine_index

if TYPE_CHECKING:
    from app.users.models import User


# --- Enums -------------------------------------------------------------------------


class DegreeLevel(StrEnum):
    HIGH_SCHOOL = "high_school"
    CERTIFICATE = "certificate"
    DIPLOMA = "diploma"
    ASSOCIATE = "associate"
    BACHELOR = "bachelor"
    MASTER = "master"
    DOCTORATE = "doctorate"
    OTHER = "other"


class SkillCategory(StrEnum):
    PROGRAMMING_LANGUAGE = "programming_language"
    FRAMEWORK = "framework"
    LIBRARY = "library"
    TOOL = "tool"
    PLATFORM = "platform"
    DATABASE = "database"
    CLOUD = "cloud"
    METHODOLOGY = "methodology"
    DOMAIN = "domain"
    SOFT_SKILL = "soft_skill"
    LANGUAGE = "language"
    OTHER = "other"


class ProficiencyLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    INTERNSHIP = "internship"
    CONTRACT = "contract"
    FREELANCE = "freelance"
    VOLUNTEER = "volunteer"
    OTHER = "other"


class WorkplaceType(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"


class ExperienceLevel(StrEnum):
    STUDENT = "student"
    ENTRY_LEVEL = "entry_level"
    JUNIOR = "junior"
    MID_LEVEL = "mid_level"
    SENIOR = "senior"
    LEAD = "lead"


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"


class EvidenceSourceType(StrEnum):
    """What the evidence is about. Each value except PROFILE has a matching FK column."""

    PROJECT = "project"
    WORK_EXPERIENCE = "work_experience"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    ACHIEVEMENT = "achievement"
    COURSEWORK = "coursework"
    PROFILE = "profile"  # general statement not tied to one item


class EvidenceOrigin(StrEnum):
    """How the evidence entered the system."""

    USER_ENTERED = "user_entered"
    RESUME_EXTRACTED = "resume_extracted"
    AI_SUGGESTED = "ai_suggested"  # only ever created by accepting a ProfileSuggestion


class SuggestionSection(StrEnum):
    PERSONAL_INFO = "personal_info"
    EDUCATION = "education"
    WORK_EXPERIENCE = "work_experience"
    PROJECT = "project"
    CERTIFICATION = "certification"
    ACHIEVEMENT = "achievement"
    COURSEWORK = "coursework"
    SKILL = "skill"
    EVIDENCE = "evidence"


class SuggestionAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"


class SuggestionSource(StrEnum):
    RESUME_EXTRACTION = "resume_extraction"
    AI_GENERATION = "ai_generation"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# Evidence subject type -> FK column on candidate_evidence.
EVIDENCE_SUBJECT_COLUMNS: dict[EvidenceSourceType, str] = {
    EvidenceSourceType.PROJECT: "project_id",
    EvidenceSourceType.WORK_EXPERIENCE: "work_experience_id",
    EvidenceSourceType.EDUCATION: "education_id",
    EvidenceSourceType.CERTIFICATION: "certification_id",
    EvidenceSourceType.ACHIEVEMENT: "achievement_id",
    EvidenceSourceType.COURSEWORK: "coursework_id",
}


def _date_range(start: str, end: str) -> CheckConstraint:
    return CheckConstraint(f"{end} IS NULL OR {start} IS NULL OR {end} >= {start}", "date_range")


# --- Profile -----------------------------------------------------------------------


class CandidateProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidate_profiles"

    user_id: Mapped[uuid.UUID] = fk_column("users.id", index=False)
    full_name: Mapped[str] = mapped_column(String(200))
    headline: Mapped[str | None] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(200))
    website_url: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    github_url: Mapped[str | None] = mapped_column(Text)

    # Job-search preferences (user-provided, like everything on this table).
    preferred_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), default=list, server_default=text("'{}'")
    )
    preferred_locations: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), default=list, server_default=text("'{}'")
    )
    work_modes: Mapped[list[str]] = enum_array_column(WorkplaceType)
    job_types: Mapped[list[str]] = enum_array_column(EmploymentType)
    experience_level: Mapped[ExperienceLevel | None] = enum_column(ExperienceLevel)

    __table_args__ = (UniqueConstraint("user_id"),)  # one master profile per user

    user: Mapped[User] = relationship(back_populates="profile")
    educations: Mapped[list[Education]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    work_experiences: Mapped[list[WorkExperience]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    projects: Mapped[list[Project]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    certifications: Mapped[list[Certification]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    achievements: Mapped[list[Achievement]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    coursework: Mapped[list[Coursework]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    skills: Mapped[list[CandidateSkill]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    evidence: Mapped[list[CandidateEvidence]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    resumes: Mapped[list[Resume]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    suggestions: Mapped[list[ProfileSuggestion]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )


class Education(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "educations"
    __table_args__ = (
        _date_range("start_date", "end_date"),
        CheckConstraint("gpa >= 0 AND (gpa_scale IS NULL OR gpa <= gpa_scale)", "gpa_valid"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id")
    institution: Mapped[str] = mapped_column(String(300))
    degree: Mapped[str | None] = mapped_column(String(200))
    degree_level: Mapped[DegreeLevel | None] = enum_column(DegreeLevel)
    field_of_study: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    gpa: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    gpa_scale: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    profile: Mapped[CandidateProfile] = relationship(back_populates="educations")
    # The DB sets coursework.education_id to NULL (ON DELETE SET NULL).
    coursework: Mapped[list[Coursework]] = relationship(
        back_populates="education", passive_deletes=True
    )
    evidence: Mapped[list[CandidateEvidence]] = relationship(
        back_populates="education", cascade="all, delete-orphan", passive_deletes=True
    )


class WorkExperience(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_experiences"
    __table_args__ = (
        _date_range("start_date", "end_date"),
        CheckConstraint("NOT (is_current AND end_date IS NOT NULL)", "current_has_no_end"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id")
    company_name: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(200))
    employment_type: Mapped[EmploymentType | None] = enum_column(EmploymentType)
    location: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    profile: Mapped[CandidateProfile] = relationship(back_populates="work_experiences")
    evidence: Mapped[list[CandidateEvidence]] = relationship(
        back_populates="work_experience", cascade="all, delete-orphan", passive_deletes=True
    )


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (_date_range("start_date", "end_date"),)

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id")
    title: Mapped[str] = mapped_column(String(300))
    role: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    project_url: Mapped[str | None] = mapped_column(Text)
    repository_url: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    profile: Mapped[CandidateProfile] = relationship(back_populates="projects")
    evidence: Mapped[list[CandidateEvidence]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class Certification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "certifications"
    __table_args__ = (_date_range("issue_date", "expiration_date"),)

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id")
    name: Mapped[str] = mapped_column(String(300))
    issuer: Mapped[str | None] = mapped_column(String(200))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiration_date: Mapped[date | None] = mapped_column(Date)
    credential_id: Mapped[str | None] = mapped_column(String(200))
    credential_url: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    profile: Mapped[CandidateProfile] = relationship(back_populates="certifications")
    evidence: Mapped[list[CandidateEvidence]] = relationship(
        back_populates="certification", cascade="all, delete-orphan", passive_deletes=True
    )


class Achievement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "achievements"

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id")
    title: Mapped[str] = mapped_column(String(300))
    issuer: Mapped[str | None] = mapped_column(String(200))
    achieved_on: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    profile: Mapped[CandidateProfile] = relationship(back_populates="achievements")
    evidence: Mapped[list[CandidateEvidence]] = relationship(
        back_populates="achievement", cascade="all, delete-orphan", passive_deletes=True
    )


class Coursework(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A course, optionally tied to an education entry (standalone for e.g. online courses)."""

    __tablename__ = "coursework"

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id")
    education_id: Mapped[uuid.UUID | None] = fk_column(
        "educations.id", ondelete="SET NULL", nullable=True
    )
    course_name: Mapped[str] = mapped_column(String(300))
    course_code: Mapped[str | None] = mapped_column(String(50))
    term: Mapped[str | None] = mapped_column(String(100))
    grade: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    profile: Mapped[CandidateProfile] = relationship(back_populates="coursework")
    education: Mapped[Education | None] = relationship(back_populates="coursework")
    evidence: Mapped[list[CandidateEvidence]] = relationship(
        back_populates="coursework", cascade="all, delete-orphan", passive_deletes=True
    )


# --- Skills ------------------------------------------------------------------------


class Skill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Shared skill vocabulary, referenced by candidates and job requirements alike."""

    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(
            "normalized_name = lower(btrim(normalized_name)) AND normalized_name <> ''",
            "normalized_name_format",
        ),
    )

    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[SkillCategory | None] = enum_column(SkillCategory)


class CandidateSkill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A skill the candidate lists. Proof that they have it lives in evidence rows linked
    through ``candidate_evidence_skills``."""

    __tablename__ = "candidate_skills"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", "skill_id"),
        CheckConstraint("years_experience >= 0", "years_experience_non_negative"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    skill_id: Mapped[uuid.UUID] = fk_column("skills.id", ondelete=None)
    proficiency: Mapped[ProficiencyLevel | None] = enum_column(ProficiencyLevel)
    years_experience: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))

    profile: Mapped[CandidateProfile] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship()


# --- Uploaded resumes --------------------------------------------------------------


class Resume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An original resume document uploaded by the candidate (input, not generated)."""

    __tablename__ = "resumes"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", "sha256"),
        CheckConstraint("file_size_bytes > 0", "file_size_positive"),
        # At most one primary resume per profile.
        Index(
            "uq_resumes_primary_per_profile",
            "candidate_profile_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    file_name: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[DocumentFormat] = enum_column(DocumentFormat)
    storage_key: Mapped[str] = mapped_column(Text)  # location in file storage, never a URL
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    parse_status: Mapped[ParseStatus] = enum_column(
        ParseStatus, default=ParseStatus.PENDING, server_default="pending"
    )
    parse_error: Mapped[str | None] = mapped_column(Text)
    parsed_text: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    profile: Mapped[CandidateProfile] = relationship(back_populates="resumes")


# --- Evidence (source of truth) ----------------------------------------------------

candidate_evidence_skills = Table(
    "candidate_evidence_skills",
    Base.metadata,
    Column(
        "evidence_id",
        ForeignKey("candidate_evidence.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("skill_id", ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True, index=True),
)


def _subject_matches_type(source_type: EvidenceSourceType, column: str) -> CheckConstraint:
    # The FK for this subject is set if and only if source_type selects it. Together these
    # constraints guarantee at most one subject FK, matching source_type.
    return CheckConstraint(
        f"(source_type = '{source_type.value}') = ({column} IS NOT NULL)",
        f"{column.removesuffix('_id')}_matches_type",
    )


class CandidateEvidence(UUIDPrimaryKeyMixin, TimestampMixin, EmbeddingMixin, Base):
    """A single, verifiable statement about the candidate. The source of truth for claims.

    Invariant (service layer): the subject FK and ``source_resume_id`` must belong to the
    same ``candidate_profile_id``.
    """

    __tablename__ = "candidate_evidence"
    __table_args__ = (
        *(_subject_matches_type(t, c) for t, c in EVIDENCE_SUBJECT_COLUMNS.items()),
        # A resume source implies extraction. The reverse is not required: extracted evidence
        # the candidate confirmed survives deletion of the uploaded file (SET NULL).
        CheckConstraint(
            "source_resume_id IS NULL OR origin = 'resume_extracted'",
            "source_resume_implies_extracted",
        ),
        CheckConstraint("length(btrim(content)) > 0", "content_not_blank"),
        Index("ix_candidate_evidence_profile_source", "candidate_profile_id", "source_type"),
        hnsw_cosine_index("candidate_evidence"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    source_type: Mapped[EvidenceSourceType] = enum_column(EvidenceSourceType)
    origin: Mapped[EvidenceOrigin] = enum_column(EvidenceOrigin)
    content: Mapped[str] = mapped_column(Text)

    project_id: Mapped[uuid.UUID | None] = fk_column("projects.id", nullable=True)
    work_experience_id: Mapped[uuid.UUID | None] = fk_column("work_experiences.id", nullable=True)
    education_id: Mapped[uuid.UUID | None] = fk_column("educations.id", nullable=True)
    certification_id: Mapped[uuid.UUID | None] = fk_column("certifications.id", nullable=True)
    achievement_id: Mapped[uuid.UUID | None] = fk_column("achievements.id", nullable=True)
    coursework_id: Mapped[uuid.UUID | None] = fk_column("coursework.id", nullable=True)

    # Provenance: the uploaded resume this was extracted from (origin=resume_extracted).
    source_resume_id: Mapped[uuid.UUID | None] = fk_column(
        "resumes.id", ondelete="SET NULL", nullable=True
    )
    # Extracted evidence must be confirmed by the candidate before claims may cite it.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[CandidateProfile] = relationship(back_populates="evidence")
    project: Mapped[Project | None] = relationship(back_populates="evidence")
    work_experience: Mapped[WorkExperience | None] = relationship(back_populates="evidence")
    education: Mapped[Education | None] = relationship(back_populates="evidence")
    certification: Mapped[Certification | None] = relationship(back_populates="evidence")
    achievement: Mapped[Achievement | None] = relationship(back_populates="evidence")
    coursework: Mapped[Coursework | None] = relationship(back_populates="evidence")
    source_resume: Mapped[Resume | None] = relationship()
    skills: Mapped[list[Skill]] = relationship(secondary=candidate_evidence_skills)


# --- AI suggestions (kept apart from the master profile) ---------------------------


class ProfileSuggestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A proposed change to the master profile produced by AI (e.g. resume extraction).

    Suggestions never modify the profile by themselves. They are applied only when the
    candidate accepts one, through the same validated code path as a manual edit.
    ``target_id`` / ``applied_target_id`` point into the table named by ``section`` (no FK,
    as the target table varies).
    """

    __tablename__ = "profile_suggestions"
    __table_args__ = (
        CheckConstraint(
            "(action = 'update') = (target_id IS NOT NULL) OR section = 'personal_info'",
            "update_has_target",
        ),
        CheckConstraint("(status = 'pending') = (reviewed_at IS NULL)", "reviewed_when_decided"),
        Index("ix_profile_suggestions_profile_status", "candidate_profile_id", "status"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    section: Mapped[SuggestionSection] = enum_column(SuggestionSection)
    action: Mapped[SuggestionAction] = enum_column(SuggestionAction)
    target_id: Mapped[uuid.UUID | None] = mapped_column()
    proposed_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[SuggestionSource] = enum_column(SuggestionSource)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SuggestionStatus] = enum_column(
        SuggestionStatus, default=SuggestionStatus.PENDING, server_default="pending"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_target_id: Mapped[uuid.UUID | None] = mapped_column()
    ai_execution_log_id: Mapped[uuid.UUID | None] = fk_column(
        "ai_execution_logs.id", ondelete="SET NULL", nullable=True
    )

    profile: Mapped[CandidateProfile] = relationship(back_populates="suggestions")
