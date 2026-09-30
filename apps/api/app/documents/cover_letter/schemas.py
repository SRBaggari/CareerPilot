import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.documents.cover_letter.content import CoverLetterContent
from app.documents.models import DocumentStatus, VerificationVerdict
from app.documents.resume.schemas import CitedEvidence
from app.verification.types import VerificationReportOut


class LetterAuditItem(BaseModel):
    """A generated sentence that wasn't kept as written (decided during generation)."""

    section: str  # "Paragraph 2", "Greeting", "Closing"
    original_text: str
    final_text: str | None  # the regenerated sentence, or None if it was removed
    outcome: Literal["regenerated", "removed"]
    verdict: VerificationVerdict
    reason: str


class GenerationChanges(BaseModel):
    kept: int
    regenerated: int
    removed: int
    audit: list[LetterAuditItem]


class CoverLetterOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    company_name: str
    version: int
    status: DocumentStatus
    generator: str | None
    created_at: datetime
    updated_at: datetime
    content: CoverLetterContent
    word_count: int
    changes: GenerationChanges
    notes: list[str]
    evidence: dict[uuid.UUID, CitedEvidence]  # every evidence row the letter or report cites
    report: VerificationReportOut | None  # the latest independent verification


class CoverLetterEdit(BaseModel):
    """The candidate's edited letter as plain text. Job title, company and signature come
    from the job and profile; every sentence is verified before anything is saved."""

    greeting: str = Field(min_length=1, max_length=200)
    paragraphs: list[str] = Field(min_length=1, max_length=8)
    closing: str = Field(min_length=1, max_length=100)
