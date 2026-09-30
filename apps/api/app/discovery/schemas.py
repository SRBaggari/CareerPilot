import uuid

from pydantic import BaseModel

from app.discovery.models import NormalizedJob


class SourceOut(BaseModel):
    name: str
    display_name: str
    access_kind: str | None
    description: str
    terms_url: str | None
    enabled: bool
    reasons: list[str]


class DiscoveredJobOut(NormalizedJob):
    matched_skills: list[str]  # which of the requested skills the posting names
    imported_job_id: uuid.UUID | None  # set once you have imported this posting


class DiscoveryResultsOut(BaseModel):
    jobs: list[DiscoveredJobOut]
    total: int
    page: int
    page_size: int
    sources: list[SourceOut]
    errors: list[str]  # sources that failed or refused access; other sources still answer


class ImportResultOut(BaseModel):
    job_id: uuid.UUID
    created: bool  # False when you had already imported this posting
