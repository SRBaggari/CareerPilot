import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.documents.models import DocumentStatus, VerificationVerdict
from app.documents.resume.content import ResumeContent
from app.verification.types import VerificationReportOut


class AuditItem(BaseModel):
    """A generated claim that was not kept as written (decided during generation)."""

    section: str
    original_text: str
    final_text: str | None  # the rewrite (the cited evidence verbatim), or None if rejected
    outcome: Literal["rewritten", "rejected"]
    verdict: VerificationVerdict
    reason: str


class VerificationSummary(BaseModel):
    verified_claims: int
    rewritten: int
    rejected: int
    audit: list[AuditItem]


class CitedEvidence(BaseModel):
    content: str
    record_label: str | None


class TailoredResumeOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    company_name: str
    version: int
    status: DocumentStatus
    generator: str | None
    created_at: datetime
    updated_at: datetime
    content: ResumeContent
    verification: VerificationSummary
    notes: list[str]
    evidence: dict[uuid.UUID, CitedEvidence]  # every evidence row the resume or report cites
    report: VerificationReportOut | None  # the latest independent verification of the resume


class TailoredResumeEdit(BaseModel):
    """The candidate's edited resume. Record facts are re-derived from the profile by ID;
    every claim is re-verified against the evidence it cites."""

    content: ResumeContent
