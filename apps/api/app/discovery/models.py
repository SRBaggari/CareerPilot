"""The provider-independent job model and search query."""

from datetime import date

from pydantic import BaseModel, Field, field_validator

from app.profiles.models import EmploymentType, ExperienceLevel, WorkplaceType

WorkMode = WorkplaceType  # onsite / hybrid / remote


class NormalizedJob(BaseModel):
    """A job posting in CareerPilot's shape, whatever source it came from."""

    source: str  # the provider's name, e.g. "mock"
    source_identifier: str = Field(min_length=1, max_length=200)  # the provider's own ID
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    url: str | None = None  # the original posting
    description: str = Field(min_length=1)
    employment_type: EmploymentType | None = None
    work_mode: WorkMode | None = None
    posted_date: date | None = None
    deadline: date | None = None
    # Used for filtering. Providers may supply them; otherwise they are derived from the
    # posting itself (explicit wording only).
    skills: list[str] = Field(default_factory=list)
    experience_level: ExperienceLevel | None = None

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("A posting URL must be an http(s) URL.")
        return value


class JobSearchQuery(BaseModel):
    """Search filters. Every provider receives the full query (and may use it to narrow
    its request); the core applies the same filters to every provider's results."""

    role: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    remote: bool | None = None  # True: remote only; False: not remote; None: either
    employment_types: list[EmploymentType] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list, max_length=20)
    experience_levels: list[ExperienceLevel] = Field(default_factory=list)
    source: str | None = None  # limit the search to one provider
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=50)
