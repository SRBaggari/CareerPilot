"""Request/response schemas for job analysis."""

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.jobs.models import JobInputMethod, RequirementImportance, RequirementType, SalaryPeriod
from app.profiles.models import EmploymentType, WorkplaceType
from app.profiles.schemas import InputModel, OptionalDate, OptionalUrl


class JobAnalyzeIn(InputModel):
    """A pasted job description. ``source_url`` is stored for reference only; it is never
    fetched (automatic scraping is not supported)."""

    description: str = Field(min_length=50, max_length=50_000)
    source_url: OptionalUrl = None
    # Optional corrections; they take precedence over extracted values.
    title: str | None = Field(default=None, max_length=300)
    company_name: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)


class RequirementIn(InputModel):
    requirement_type: RequirementType
    importance: RequirementImportance
    description: str = Field(min_length=1, max_length=2000)
    min_years: Decimal | None = Field(default=None, ge=0, le=60, max_digits=3, decimal_places=1)


class JobManualIn(InputModel):
    """A job entered field by field. Nothing is extracted or inferred."""

    title: str = Field(min_length=1, max_length=300)
    company_name: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    workplace_type: WorkplaceType | None = None
    employment_type: EmploymentType | None = None
    description: str | None = Field(default=None, max_length=50_000)
    source_url: OptionalUrl = None
    salary_min: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    salary_max: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    salary_currency: str | None = None
    salary_period: SalaryPeriod | None = None
    salary_text: str | None = Field(default=None, max_length=300)
    application_deadline: OptionalDate = None
    requirements: list[RequirementIn] = Field(default_factory=list, max_length=200)

    @field_validator("salary_currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z]{3}", value):
            raise ValueError("must be a 3-letter currency code, e.g. USD or INR")
        return value.upper() if value else value

    @model_validator(mode="after")
    def _salary_range(self) -> Self:
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_max < self.salary_min
        ):
            raise ValueError("salary_max must not be less than salary_min")
        return self


class SalaryOut(BaseModel):
    text: str | None
    minimum: Decimal | None
    maximum: Decimal | None
    currency: str | None
    period: SalaryPeriod | None


class RequirementOut(BaseModel):
    id: uuid.UUID
    requirement_type: RequirementType
    importance: RequirementImportance
    description: str
    source_excerpt: str | None  # verbatim text from the job description
    min_years: Decimal | None
    skill_id: uuid.UUID | None


class RequirementCounts(BaseModel):
    required: int
    preferred: int
    informational: int


class JobSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    company_name: str
    location: str | None
    workplace_type: WorkplaceType | None
    employment_type: EmploymentType | None
    application_deadline: date | None
    input_method: JobInputMethod | None
    requirement_counts: RequirementCounts
    created_at: datetime


class JobOut(JobSummaryOut):
    source_url: str | None
    description: str | None
    salary: SalaryOut | None
    analyzer_name: str | None
    analysis_warnings: list[str]
    analyzed_at: datetime | None
    requirements: list[RequirementOut]
