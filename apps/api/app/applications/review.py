"""Review, approve or reject an application: the candidate's explicit decisions.

States: draft → ready_for_review → approved → submitted, or rejected (from review or from
an approval). An approval is bound to the content hash the candidate reviewed; when the
content changes afterwards, the approval is withdrawn and the application needs review
again.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.applications import approval
from app.applications import service as applications
from app.applications.approval import ReviewPackage
from app.applications.models import (
    ApplicationStatus,
    ApplicationStatusHistory,
    ApprovalDecision,
    ApprovalState,
    StatusActor,
)
from app.core.config import Settings
from app.core.errors import ConflictError, FieldValidationError
from app.documents.models import DocumentStatus
from app.users.models import User


class ApprovalIn(BaseModel):
    """Your explicit approval of the exact content version you reviewed."""

    model_config = ConfigDict(extra="forbid")  # the reviewer is you; it can't be supplied

    content_hash: str = Field(min_length=64, max_length=64)
    confirm: bool
    note: str | None = Field(default=None, max_length=2000)


class RejectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)


async def _package(session: AsyncSession, user: User, application_id: uuid.UUID) -> ReviewPackage:
    a = await applications._owned(session, user, application_id)
    package, _ = await approval.build(session, user, a)
    return package


async def get_review(session: AsyncSession, user: User, application_id: uuid.UUID) -> ReviewPackage:
    """Everything to review before submitting. Opening it is recorded, and never approves."""
    a = await applications._owned(session, user, application_id)
    _, snapshot = await approval.build(session, user, a)
    await approval.invalidate_if_changed(session, user, a, snapshot)
    await approval.audit(
        session,
        a,
        "review_opened",
        "You opened the review. Opening it doesn't approve anything.",
        user=user,
        content_hash=approval.content_hash(snapshot),
    )
    await session.commit()
    return await _package(session, user, application_id)


async def request_review(
    session: AsyncSession, user: User, application_id: uuid.UUID
) -> ReviewPackage:
    a = await applications._owned(session, user, application_id)
    if a.approval_state not in (ApprovalState.DRAFT, ApprovalState.REJECTED):
        raise ConflictError(
            {
                ApprovalState.READY_FOR_REVIEW: "It's already ready for your review.",
                ApprovalState.APPROVED: "It's already approved.",
                ApprovalState.SUBMITTED: "It's already submitted.",
            }[a.approval_state]
        )
    if a.status not in approval.PREPARED:
        raise ConflictError("Move the application to “Application prepared” first.")
    a.approval_state = ApprovalState.READY_FOR_REVIEW
    if a.status != ApplicationStatus.AWAITING_APPROVAL:
        applications._history(a, ApplicationStatus.AWAITING_APPROVAL, StatusActor.USER, None)
    await approval.audit(
        session, a, "review_requested", "You marked the application ready for review.", user=user
    )
    await session.commit()
    return await _package(session, user, application_id)


async def approve(
    session: AsyncSession,
    user: User,
    application_id: uuid.UUID,
    payload: ApprovalIn,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> ReviewPackage:
    if not payload.confirm:
        raise FieldValidationError(
            "confirm", "Confirm that you reviewed the application and approve it."
        )
    a = await applications._owned(session, user, application_id)
    if a.approval_state != ApprovalState.READY_FOR_REVIEW:
        raise ConflictError(
            "Only an application that's ready for review can be approved"
            + (" (it's already approved)." if a.approval_state == ApprovalState.APPROVED else ".")
        )
    # Verified against the evidence as it is now, not as it was when the documents were
    # generated (a deleted or edited record must not stay "verified").
    await approval.reverify_documents(session, user, a, embedder, llm, settings)
    await approval.lock(session, a)
    if a.approval_state != ApprovalState.READY_FOR_REVIEW:
        raise ConflictError("The application changed while it was being checked. Review it again.")
    package, snapshot = await approval.build(session, user, a)
    if payload.content_hash != package.content_hash:
        raise ConflictError(
            "The application changed since you opened the review. Review it again before approving."
        )
    blockers = [i.message for i in package.issues if i.severity == "blocker"]
    if blockers:
        await approval.audit(
            session,
            a,
            "approval_blocked",
            "Approval refused: " + " ".join(blockers),
            user=user,
            reasons=blockers,
        )
        await session.commit()
        raise ConflictError("Not ready to approve: " + " ".join(blockers))
    now = approval._now()
    version = await approval.record_decision(
        session, a, user, ApprovalDecision.APPROVED, snapshot, payload.note
    )
    a.approval_state = ApprovalState.APPROVED
    a.approved_at, a.approved_by_id, a.approved_content_hash = now, user.id, package.content_hash
    for doc in (a.tailored_resume, a.cover_letter):
        if doc is not None and doc.status != DocumentStatus.APPROVED:
            doc.status, doc.approved_at = DocumentStatus.APPROVED, now
    a.status_history.append(
        ApplicationStatusHistory(
            from_status=a.status,
            to_status=a.status,
            actor=StatusActor.USER,
            note=f"Approved by you (version {version}). It isn't submitted until you submit it.",
        )
    )
    await approval.audit(
        session,
        a,
        "approved",
        f"You approved version {version} of the application.",
        user=user,
        version=version,
        content_hash=package.content_hash,
    )
    await session.commit()
    return await _package(session, user, application_id)


async def reject(
    session: AsyncSession, user: User, application_id: uuid.UUID, payload: RejectionIn
) -> ReviewPackage:
    a = await applications._owned(session, user, application_id)
    await approval.lock(session, a)
    await approval.ensure_not_submitting(session, a)
    if a.approval_state not in (ApprovalState.READY_FOR_REVIEW, ApprovalState.APPROVED):
        raise ConflictError("Only an application under review or approved can be rejected.")
    _, snapshot = await approval.build(session, user, a)
    was_approved = a.approval_state == ApprovalState.APPROVED
    version = await approval.record_decision(
        session, a, user, ApprovalDecision.REJECTED, snapshot, payload.note
    )
    approval.clear_approval(a, ApprovalState.REJECTED)
    await approval.audit(
        session,
        a,
        "rejected",
        "You withdrew your approval and rejected the application."
        if was_approved
        else "You rejected the application.",
        user=user,
        version=version,
        note=payload.note,
    )
    await session.commit()
    return await _package(session, user, application_id)
