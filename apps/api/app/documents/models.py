"""Generated application documents and the claims inside them.

Traceability chain::

    TailoredResume / CoverLetter
        -> GeneratedClaim            (one statement in the document)
            -> generated_claim_evidence -> CandidateEvidence   (why it is true)
            -> ClaimVerification     (history of checks of that link)

Cited evidence cannot be deleted while a claim references it. The FK is deferred to commit
so that deleting a whole profile (which removes claims and evidence together) still works.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    fk_column,
)
from app.jobs.models import Job
from app.profiles.models import CandidateEvidence, DocumentFormat, Resume


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    VERIFICATION_FAILED = "verification_failed"
    VERIFIED = "verified"  # every claim is supported by evidence
    APPROVED = "approved"  # the candidate explicitly approved it
    ARCHIVED = "archived"


class ClaimStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    UNSUPPORTED = "unsupported"
    REMOVED = "removed"  # dropped from the document (e.g. after failing verification)


class VerificationVerdict(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class VerificationMethod(StrEnum):
    RULE_BASED = "rule_based"
    LLM = "llm"
    HUMAN = "human"


_APPROVED_HAS_TIMESTAMP = CheckConstraint(
    "status <> 'approved' OR approved_at IS NOT NULL", "approved_has_timestamp"
)


class TailoredResume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tailored_resumes"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", "job_id", "version"),
        CheckConstraint("version >= 1", "version_positive"),
        _APPROVED_HAS_TIMESTAMP,
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    job_id: Mapped[uuid.UUID] = fk_column("jobs.id", ondelete=None)
    base_resume_id: Mapped[uuid.UUID | None] = fk_column(
        "resumes.id", ondelete="SET NULL", nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[DocumentStatus] = enum_column(
        DocumentStatus, default=DocumentStatus.DRAFT, server_default="draft"
    )
    # Structured sections (summary, experience bullets, ...) used to render the file.
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    file_format: Mapped[DocumentFormat | None] = enum_column(DocumentFormat)
    storage_key: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_execution_log_id: Mapped[uuid.UUID | None] = fk_column(
        "ai_execution_logs.id", ondelete="SET NULL", nullable=True
    )
    generator_name: Mapped[str | None] = mapped_column(String(50))  # "rules" / "llm:<model>"
    # Notes for the candidate, e.g. skills left out because no evidence supports them.
    notes: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    job: Mapped[Job] = relationship()
    base_resume: Mapped[Resume | None] = relationship()
    claims: Mapped[list[GeneratedClaim]] = relationship(
        back_populates="tailored_resume", cascade="all, delete-orphan", passive_deletes=True
    )


class CoverLetter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cover_letters"
    __table_args__ = (
        UniqueConstraint("candidate_profile_id", "job_id", "version"),
        CheckConstraint("version >= 1", "version_positive"),
        _APPROVED_HAS_TIMESTAMP,
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id", index=False)
    job_id: Mapped[uuid.UUID] = fk_column("jobs.id", ondelete=None)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[DocumentStatus] = enum_column(
        DocumentStatus, default=DocumentStatus.DRAFT, server_default="draft"
    )
    # Structured letter (greeting, paragraphs of sentences with evidence IDs, closing).
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    file_format: Mapped[DocumentFormat | None] = enum_column(DocumentFormat)
    storage_key: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_execution_log_id: Mapped[uuid.UUID | None] = fk_column(
        "ai_execution_logs.id", ondelete="SET NULL", nullable=True
    )
    generator_name: Mapped[str | None] = mapped_column(String(50))  # "rules" / "llm:<model>"
    notes: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    job: Mapped[Job] = relationship()
    claims: Mapped[list[GeneratedClaim]] = relationship(
        back_populates="cover_letter", cascade="all, delete-orphan", passive_deletes=True
    )


generated_claim_evidence = Table(
    "generated_claim_evidence",
    Base.metadata,
    Column(
        "generated_claim_id",
        ForeignKey("generated_claims.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # NO ACTION, checked at commit: evidence cited by a claim cannot be deleted on its own,
    # but a cascade that also removes the citing claims (e.g. account deletion) succeeds.
    Column(
        "evidence_id",
        ForeignKey("candidate_evidence.id", deferrable=True, initially="DEFERRED"),
        primary_key=True,
        index=True,
    ),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


class GeneratedClaim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One factual statement in a generated document, citing the evidence behind it.

    Invariants (service layer): a VERIFIED claim cites at least one evidence row, and all
    cited evidence belongs to the document's candidate profile.
    """

    __tablename__ = "generated_claims"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(tailored_resume_id, cover_letter_id) = 1", "exactly_one_document"
        ),
        CheckConstraint("length(btrim(claim_text)) > 0", "claim_text_not_blank"),
    )

    tailored_resume_id: Mapped[uuid.UUID | None] = fk_column("tailored_resumes.id", nullable=True)
    cover_letter_id: Mapped[uuid.UUID | None] = fk_column("cover_letters.id", nullable=True)
    claim_text: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String(50))  # e.g. "summary", "experience"
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[ClaimStatus] = enum_column(
        ClaimStatus, default=ClaimStatus.PENDING, server_default="pending"
    )

    tailored_resume: Mapped[TailoredResume | None] = relationship(back_populates="claims")
    cover_letter: Mapped[CoverLetter | None] = relationship(back_populates="claims")
    evidence: Mapped[list[CandidateEvidence]] = relationship(secondary=generated_claim_evidence)
    verifications: Mapped[list[ClaimVerification]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ClaimVerification.created_at",
    )


class ClaimVerification(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """An append-only record of one verification pass over a claim."""

    __tablename__ = "claim_verifications"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", "confidence_range"),
        Index("ix_claim_verifications_claim_created", "generated_claim_id", "created_at"),
    )

    generated_claim_id: Mapped[uuid.UUID] = fk_column("generated_claims.id", index=False)
    verdict: Mapped[VerificationVerdict] = enum_column(VerificationVerdict)
    method: Mapped[VerificationMethod] = enum_column(VerificationMethod)
    confidence: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    ai_execution_log_id: Mapped[uuid.UUID | None] = fk_column(
        "ai_execution_logs.id", ondelete="SET NULL", nullable=True
    )

    claim: Mapped[GeneratedClaim] = relationship(back_populates="verifications")
