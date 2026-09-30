"""Analyzer output. Everything here must be explicitly stated in the job description."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.jobs.models import RequirementImportance, RequirementType, SalaryPeriod
from app.profiles.models import EmploymentType, WorkplaceType

IMPORTANCE_RANK = {
    RequirementImportance.REQUIRED: 2,
    RequirementImportance.PREFERRED: 1,
    RequirementImportance.INFORMATIONAL: 0,
}


@dataclass
class ExtractedRequirement:
    requirement_type: RequirementType
    importance: RequirementImportance
    description: str  # the statement (or, for TECHNOLOGY, the technology name)
    source_excerpt: str  # verbatim job-description text it comes from
    min_years: Decimal | None = None


@dataclass
class SalaryInfo:
    text: str  # verbatim
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    currency: str | None = None  # ISO 4217, only when explicit (a bare "$" is ambiguous)
    period: SalaryPeriod | None = None


@dataclass
class JobExtraction:
    title: str | None = None
    company_name: str | None = None
    location: str | None = None
    workplace_type: WorkplaceType | None = None
    employment_type: EmploymentType | None = None
    salary: SalaryInfo | None = None
    application_deadline: date | None = None
    deadline_text: str | None = None  # verbatim text the deadline was read from
    requirements: list[ExtractedRequirement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
