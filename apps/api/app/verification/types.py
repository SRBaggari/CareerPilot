"""What the engine consumes (claims) and produces (per-claim results and a report)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel

from app.documents.models import VerificationVerdict
from app.verification.models import VerificationOutcome, VerificationTrigger


class ClaimType(StrEnum):
    # Generated statements, checked against evidence.
    SUMMARY = "summary"
    SKILL = "skill"
    EXPERIENCE = "experience"  # a bullet under a job
    PROJECT = "project"  # a bullet under a project
    STATEMENT = "statement"  # free text (e.g. pasted into the checker)
    LETTER = "letter"  # a cover letter sentence: a factual claim, or intent / courtesy
    # Record facts, checked against the stored profile.
    CONTACT = "contact"
    EMPLOYMENT = "employment"
    PROJECT_ENTRY = "project_entry"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    ACHIEVEMENT = "achievement"
    COURSEWORK = "coursework"


RECORD_FACT_TYPES = frozenset(
    {
        ClaimType.CONTACT,
        ClaimType.EMPLOYMENT,
        ClaimType.PROJECT_ENTRY,
        ClaimType.EDUCATION,
        ClaimType.CERTIFICATION,
        ClaimType.ACHIEVEMENT,
        ClaimType.COURSEWORK,
    }
)
# Bullets must be backed by the evidence of the item they sit under.
RECORD_BOUND_TYPES = frozenset({ClaimType.EXPERIENCE, ClaimType.PROJECT})


@dataclass
class ClaimInput:
    """One claim extracted from a document."""

    text: str
    claim_type: ClaimType
    cited_evidence_ids: list[uuid.UUID] = field(default_factory=list)
    section: str = ""  # where it sits, e.g. "summary" or "experience:<record id>"
    position: int = 0
    record_id: uuid.UUID | None = None  # the item a bullet or record fact belongs to
    facts: dict[str, Any] | None = None  # record facts as stated in the document

    @property
    def is_record_fact(self) -> bool:
        return self.claim_type in RECORD_FACT_TYPES

    @property
    def key(self) -> str:
        """Where the claim sits; also the key the API uses for field errors."""
        return f"{self.section}[{self.position}]"


EvidenceSource = Literal["cited", "retrieved", "profile", "none"]


class ClaimResult(BaseModel):
    claim_text: str
    claim_type: ClaimType
    section: str
    position: int
    record_id: uuid.UUID | None
    cited_evidence_ids: list[uuid.UUID]
    evidence_ids: list[uuid.UUID]  # the evidence the verdict rests on
    evidence_source: EvidenceSource
    verification_status: VerificationVerdict
    confidence: float
    reason: str
    method: Literal["rule_based", "llm"]

    @property
    def approved(self) -> bool:
        return self.verification_status == VerificationVerdict.SUPPORTED

    @property
    def key(self) -> str:
        return f"{self.section}[{self.position}]"


class VerificationReportOut(BaseModel):
    id: uuid.UUID | None = None  # None for checks that aren't stored
    document_type: Literal["tailored_resume", "cover_letter", "text"]
    document_id: uuid.UUID | None = None
    trigger: VerificationTrigger | None = None
    created_at: datetime | None = None
    verifier: str
    outcome: VerificationOutcome
    counts: dict[VerificationVerdict, int]
    claims: list[ClaimResult]
    warnings: list[str] = []


def build_report(
    results: list[ClaimResult],
    *,
    verifier: str,
    document_type: Literal["tailored_resume", "cover_letter", "text"],
    warnings: list[str] | None = None,
) -> VerificationReportOut:
    counts = {v: sum(1 for r in results if r.verification_status == v) for v in VerificationVerdict}
    approved = all(r.approved for r in results)
    return VerificationReportOut(
        document_type=document_type,
        verifier=verifier,
        outcome=VerificationOutcome.APPROVED if approved else VerificationOutcome.REJECTED,
        counts=counts,
        claims=results,
        warnings=warnings or [],
    )
