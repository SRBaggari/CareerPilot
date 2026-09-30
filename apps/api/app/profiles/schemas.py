"""Request/response schemas for the candidate profile API.

Input models forbid unknown fields, so clients cannot set server-controlled provenance
(``origin``, ``confirmed_at``, ...). Blank strings become ``None``. Section item models use
full-replacement semantics (PUT); the profile itself supports PATCH.
"""

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.profiles.models import (
    DegreeLevel,
    EmploymentType,
    EvidenceOrigin,
    EvidenceSourceType,
    ExperienceLevel,
    ProficiencyLevel,
    SkillCategory,
    SuggestionAction,
    SuggestionSection,
    SuggestionSource,
    SuggestionStatus,
    WorkplaceType,
)

MAX_LIST_ITEMS = 20
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9 ()\-.]{5,30}$")


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


_UNSAFE_SCHEME_RE = re.compile(r"^\s*(javascript|data|vbscript|file)\s*:", re.IGNORECASE)
_HOSTNAME_RE = re.compile(r"^(localhost|[a-z0-9-]+(\.[a-z0-9-]+)+)$")


def normalize_url(value: str | None) -> str | None:
    """Accept only http(s) URLs with a real hostname; add ``https://`` if no scheme given."""
    if value is None:
        return None
    if len(value) > 2000:
        raise ValueError("URL is too long")
    if _UNSAFE_SCHEME_RE.match(value):
        raise ValueError("must be a valid http(s) URL")
    if "://" not in value:
        value = f"https://{value}"
    parts = urlsplit(value)
    try:
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError as exc:
        raise ValueError("must be a valid http(s) URL") from exc
    hostname = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not _HOSTNAME_RE.match(hostname) or " " in value:
        raise ValueError("must be a valid http(s) URL")
    return value


def clean_text_list(values: list[str]) -> list[str]:
    """Trim, drop blanks, and de-duplicate case-insensitively (keeping first spelling)."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        item = raw.strip()
        if not item:
            continue
        if len(item) > 200:
            raise ValueError("each entry must be at most 200 characters")
        if item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    if len(result) > MAX_LIST_ITEMS:
        raise ValueError(f"at most {MAX_LIST_ITEMS} entries allowed")
    return result


def _check_range(start: date | None, end: date | None, end_field: str) -> None:
    if start and end and end < start:
        raise ValueError(f"{end_field} must not be before the start date")


def _check_year(value: date | None) -> date | None:
    if value is not None and not 1900 <= value.year <= 2100:
        raise ValueError("date must be between 1900 and 2100")
    return value


OptionalDate = Annotated[date | None, AfterValidator(_check_year)]
OptionalUrl = Annotated[str | None, AfterValidator(normalize_url)]


# --- Profile ------------------------------------------------------------------------


class ProfileIn(InputModel):
    full_name: str = Field(min_length=1, max_length=200)
    headline: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=5000)
    contact_email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=200)
    website_url: OptionalUrl = None
    linkedin_url: OptionalUrl = None
    github_url: OptionalUrl = None
    preferred_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    work_modes: list[WorkplaceType] = Field(default_factory=list)
    job_types: list[EmploymentType] = Field(default_factory=list)
    experience_level: ExperienceLevel | None = None

    @field_validator("contact_email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL_RE.match(value):
            raise ValueError("must be a valid email address")
        return value.lower() if value else value

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        if value is not None and not _PHONE_RE.match(value):
            raise ValueError("may contain only digits, spaces, and + ( ) - .")
        return value

    @field_validator("preferred_roles", "preferred_locations", mode="before")
    @classmethod
    def _text_list(cls, value: Any) -> Any:
        if value is None:
            return []
        return clean_text_list(value) if isinstance(value, list) else value

    @field_validator("work_modes", "job_types", mode="before")
    @classmethod
    def _enum_list(cls, value: Any) -> Any:
        if value is None:
            return []
        return list(dict.fromkeys(value)) if isinstance(value, list) else value


class ProfilePatch(InputModel):
    """Partial update. Omitted fields are unchanged; ``null`` clears an optional field
    (and empties a list). The merged result is validated as a whole with ``ProfileIn``."""

    full_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    contact_email: str | None = None
    phone: str | None = None
    location: str | None = None
    website_url: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    preferred_roles: list[str] | None = None
    preferred_locations: list[str] | None = None
    work_modes: list[WorkplaceType] | None = None
    job_types: list[EmploymentType] | None = None
    experience_level: ExperienceLevel | None = None


# --- Section items --------------------------------------------------------------------


class SectionItemIn(InputModel):
    sort_order: int = Field(default=0, ge=0, le=10_000)


class EducationIn(SectionItemIn):
    institution: str = Field(min_length=1, max_length=300)
    degree: str | None = Field(default=None, max_length=200)
    degree_level: DegreeLevel | None = None
    field_of_study: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    start_date: OptionalDate = None
    end_date: OptionalDate = None
    gpa: Decimal | None = Field(default=None, ge=0, max_digits=5, decimal_places=2)
    gpa_scale: Decimal | None = Field(default=None, gt=0, le=100, max_digits=5, decimal_places=2)
    description: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _rules(self) -> Self:
        _check_range(self.start_date, self.end_date, "end_date")
        if self.gpa is not None:
            if self.gpa_scale is None:
                raise ValueError("gpa_scale is required when gpa is set")
            if self.gpa > self.gpa_scale:
                raise ValueError("gpa must not exceed gpa_scale")
        return self


class WorkExperienceIn(SectionItemIn):
    company_name: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=200)
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, max_length=200)
    start_date: OptionalDate = None
    end_date: OptionalDate = None
    is_current: bool = False
    description: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _rules(self) -> Self:
        _check_range(self.start_date, self.end_date, "end_date")
        if self.is_current and self.end_date is not None:
            raise ValueError("a current position cannot have an end_date")
        return self


class ProjectIn(SectionItemIn):
    title: str = Field(min_length=1, max_length=300)
    role: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    project_url: OptionalUrl = None
    repository_url: OptionalUrl = None
    start_date: OptionalDate = None
    end_date: OptionalDate = None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        _check_range(self.start_date, self.end_date, "end_date")
        return self


class CertificationIn(SectionItemIn):
    name: str = Field(min_length=1, max_length=300)
    issuer: str | None = Field(default=None, max_length=200)
    issue_date: OptionalDate = None
    expiration_date: OptionalDate = None
    credential_id: str | None = Field(default=None, max_length=200)
    credential_url: OptionalUrl = None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        _check_range(self.issue_date, self.expiration_date, "expiration_date")
        return self


class AchievementIn(SectionItemIn):
    title: str = Field(min_length=1, max_length=300)
    issuer: str | None = Field(default=None, max_length=200)
    achieved_on: OptionalDate = None
    description: str | None = Field(default=None, max_length=5000)
    url: OptionalUrl = None


class CourseworkIn(SectionItemIn):
    course_name: str = Field(min_length=1, max_length=300)
    course_code: str | None = Field(default=None, max_length=50)
    education_id: uuid.UUID | None = None  # must belong to the same profile (service check)
    term: str | None = Field(default=None, max_length=100)
    grade: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=5000)


# --- Evidence -------------------------------------------------------------------------


class EvidenceIn(InputModel):
    """A user-entered fact. ``origin`` is always set by the server."""

    source_type: EvidenceSourceType
    subject_id: uuid.UUID | None = None
    content: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _subject(self) -> Self:
        if self.source_type == EvidenceSourceType.PROFILE and self.subject_id is not None:
            raise ValueError("profile-level evidence must not have a subject_id")
        if self.source_type != EvidenceSourceType.PROFILE and self.subject_id is None:
            raise ValueError(f"subject_id is required for {self.source_type.value} evidence")
        return self


class EvidenceUpdate(InputModel):
    content: str = Field(min_length=1, max_length=2000)


class EvidenceOut(BaseModel):
    id: uuid.UUID
    source_type: EvidenceSourceType
    subject_id: uuid.UUID | None
    content: str
    origin: EvidenceOrigin
    confirmed_at: datetime | None
    is_cited: bool  # cited by a generated claim; cannot be deleted
    created_at: datetime
    updated_at: datetime


# --- Skills ---------------------------------------------------------------------------


class SkillIn(InputModel):
    name: str = Field(min_length=1, max_length=100)
    category: SkillCategory | None = None
    proficiency: ProficiencyLevel | None = None
    years_experience: Decimal | None = Field(
        default=None, ge=0, le=60, max_digits=3, decimal_places=1
    )


class SkillUpdate(InputModel):
    proficiency: ProficiencyLevel | None = None
    years_experience: Decimal | None = Field(
        default=None, ge=0, le=60, max_digits=3, decimal_places=1
    )


class SkillOut(BaseModel):
    id: uuid.UUID
    skill_id: uuid.UUID
    name: str
    category: SkillCategory | None
    proficiency: ProficiencyLevel | None
    years_experience: Decimal | None


# --- Outputs --------------------------------------------------------------------------


class ItemOutMixin(BaseModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    evidence: list[EvidenceOut] = Field(default_factory=list)


class EducationOut(EducationIn, ItemOutMixin):
    pass


class WorkExperienceOut(WorkExperienceIn, ItemOutMixin):
    pass


class ProjectOut(ProjectIn, ItemOutMixin):
    pass


class CertificationOut(CertificationIn, ItemOutMixin):
    pass


class AchievementOut(AchievementIn, ItemOutMixin):
    pass


class CourseworkOut(CourseworkIn, ItemOutMixin):
    pass


class ProfileOut(ProfileIn):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    educations: list[EducationOut]
    work_experiences: list[WorkExperienceOut]
    projects: list[ProjectOut]
    certifications: list[CertificationOut]
    achievements: list[AchievementOut]
    coursework: list[CourseworkOut]
    skills: list[SkillOut]
    evidence: list[EvidenceOut]  # profile-level evidence (not tied to one item)
    pending_suggestions: int


# --- AI suggestions -------------------------------------------------------------------


class SuggestionOut(BaseModel):
    """AI-generated content awaiting review. Not part of the profile until accepted."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section: SuggestionSection
    action: SuggestionAction
    target_id: uuid.UUID | None
    proposed_data: dict[str, Any]
    source: SuggestionSource
    rationale: str | None
    status: SuggestionStatus
    reviewed_at: datetime | None
    applied_target_id: uuid.UUID | None
    created_at: datetime
