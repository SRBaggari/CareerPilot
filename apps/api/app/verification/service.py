"""Verification reports: storing them, reading them back, and ad-hoc checks."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.core.config import Settings
from app.profiles import service as profiles
from app.users.models import User
from app.verification.engine import verify_claims
from app.verification.extraction import extract_text_claims
from app.verification.knowledge import load_knowledge
from app.verification.models import VerificationReport, VerificationTrigger
from app.verification.types import ClaimInput, ClaimType, VerificationReportOut

MAX_CLAIMS = 50


async def store_report(
    session: AsyncSession,
    report: VerificationReportOut,
    *,
    tailored_resume_id: uuid.UUID,
    trigger: VerificationTrigger,
    ai_execution_log_id: uuid.UUID | None,
) -> VerificationReport:
    row = VerificationReport(
        tailored_resume_id=tailored_resume_id,
        trigger=trigger,
        outcome=report.outcome,
        verifier=report.verifier[:100],
        report=report.model_dump(mode="json", include={"counts", "claims", "warnings"}),
        ai_execution_log_id=ai_execution_log_id,
        # Wall-clock time, not transaction time: several runs can share a transaction.
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


def report_out(row: VerificationReport) -> VerificationReportOut:
    return VerificationReportOut.model_validate(
        {
            **row.report,
            "id": row.id,
            "document_type": "tailored_resume",
            "document_id": row.tailored_resume_id,
            "trigger": row.trigger,
            "created_at": row.created_at,
            "verifier": row.verifier,
            "outcome": row.outcome,
        }
    )


async def reports_for(
    session: AsyncSession, tailored_resume_id: uuid.UUID
) -> list[VerificationReportOut]:
    rows = await session.scalars(
        select(VerificationReport)
        .where(VerificationReport.tailored_resume_id == tailored_resume_id)
        .order_by(VerificationReport.created_at.desc(), VerificationReport.id)
    )
    return [report_out(r) for r in rows]


# --- Ad-hoc checks ----------------------------------------------------------------------


class ClaimIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    claim_type: Literal["statement", "summary", "skill"] = "statement"
    evidence_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class ClaimCheckIn(BaseModel):
    """Claims to verify against your evidence: a list, or free text split into sentences."""

    claims: list[ClaimIn] = Field(default_factory=list, max_length=MAX_CLAIMS)
    text: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def _something_to_check(self) -> "ClaimCheckIn":
        if not self.claims and not (self.text and self.text.strip()):
            raise ValueError("Provide claims or text to verify.")
        return self


async def check_claims(
    session: AsyncSession,
    user: User,
    payload: ClaimCheckIn,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> VerificationReportOut:
    """Verify claims against the candidate's evidence. Nothing is stored."""
    profile = await profiles.get_profile(session, user)
    claims = [
        ClaimInput(c.text.strip(), ClaimType(c.claim_type), list(c.evidence_ids), "claims", n)
        for n, c in enumerate(payload.claims)
    ]
    if payload.text:
        claims += extract_text_claims(payload.text)
    claims = claims[:MAX_CLAIMS]
    knowledge = await load_knowledge(session, profile.id)
    run = await verify_claims(
        session, user, knowledge, claims, embedder, llm, settings, document_type="text"
    )
    await session.commit()  # keeps the AI execution log, if any
    return run.report
