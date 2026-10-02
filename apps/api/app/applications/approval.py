"""Human-in-the-loop approval: the review package, the content hash, and the submission gate.

The review package is everything the candidate must see before submitting: the job and
company, the resume, the cover letter, the application answers, personal information, the
evidence verification results, and any missing or uncertain fields.

An approval binds one reviewer to one exact content version (the SHA-256 of a canonical
snapshot of what will be submitted). Opening the review never approves anything. Submission
(by the candidate or by browser assistance) goes through ``ensure_can_submit``, which
refuses when the candidate hasn't approved, when the content changed after approval, when a
required field is missing, or when a claim isn't verified. Refusals are audited too.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.applications.models import (
    Application,
    ApplicationApproval,
    ApplicationAuditEvent,
    ApplicationStatus,
    ApprovalDecision,
    ApprovalState,
    StatusActor,
)
from app.automation.models import ApplicationRun, RunStatus
from app.core.config import Settings
from app.core.errors import ConflictError
from app.documents.answers.service import _sentences
from app.documents.cover_letter import service as letters
from app.documents.models import (
    ApplicationAnswer,
    ClaimStatus,
    CoverLetter,
    DocumentStatus,
    GeneratedClaim,
    TailoredResume,
)
from app.documents.resume import service as resumes
from app.profiles.models import CandidateProfile
from app.users.models import User
from app.verification import service as verification
from app.verification.models import VerificationOutcome

PREPARED = (ApplicationStatus.APPLICATION_PREPARED, ApplicationStatus.AWAITING_APPROVAL)
USABLE = (DocumentStatus.VERIFIED, DocumentStatus.APPROVED)
SECTION_LABELS = {
    "job": "the job",
    "destination": "where to apply",
    "personal": "your personal information",
    "resume": "the resume",
    "cover_letter": "the cover letter",
    "answers": "the application answers",
}


def _now() -> datetime:
    return datetime.now(UTC)


# --- Output -----------------------------------------------------------------------------------


class Issue(BaseModel):
    section: str  # job, personal, resume, cover_letter, answers, status
    severity: Literal["blocker", "warning"]  # blockers prevent approval and submission
    message: str


class UnverifiedClaim(BaseModel):
    text: str
    status: str  # the verdict, or the stored claim status
    reason: str | None = None


class Verification(BaseModel):
    """Evidence verification results for one document."""

    document_status: DocumentStatus
    verified: bool  # every claim is supported by the candidate's evidence
    outcome: VerificationOutcome | None  # of the latest verification report
    verifier: str | None
    checked_at: datetime | None
    counts: dict[str, int]  # verdict -> number of claims (latest report)
    claims_verified: int
    unverified: list[UnverifiedClaim]


class JobReview(BaseModel):
    id: uuid.UUID
    title: str
    company: str
    location: str | None
    url: str | None  # where to apply


class PersonalReview(BaseModel):
    full_name: str
    email: str
    email_source: Literal["profile", "account"]
    phone: str | None
    location: str | None
    linkedin: str | None


class DocumentReview(BaseModel):
    id: uuid.UUID
    version: int
    status: DocumentStatus
    content: dict[str, Any]
    verification: Verification


class AnswerReview(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    status: DocumentStatus
    approved: bool
    verification: Verification


class ApprovalRecord(BaseModel):
    version: int
    decision: ApprovalDecision
    reviewer: str  # the reviewer's account email
    at: datetime
    content_hash: str
    note: str | None


class AuditEventOut(BaseModel):
    id: uuid.UUID
    at: datetime
    actor: StatusActor
    user: str | None
    action: str
    message: str
    detail: dict[str, Any]


class CurrentApproval(BaseModel):
    reviewer: str | None
    approved_at: datetime
    content_hash: str
    version: int | None


class ReviewPackage(BaseModel):
    application_id: uuid.UUID
    status: ApplicationStatus
    approval_state: ApprovalState
    job: JobReview
    personal: PersonalReview
    resume: DocumentReview | None
    cover_letter: DocumentReview | None
    answers: list[AnswerReview]
    unanswered: list[str]  # questions with no answer yet
    issues: list[Issue]
    content_hash: str  # the version you're looking at; approve with this
    approval: CurrentApproval | None
    submitted_at: datetime | None
    can_request_review: bool
    can_approve: bool
    can_submit: bool
    submit_blockers: list[str]
    history: list[ApprovalRecord]
    events: list[AuditEventOut]


# --- Building the package ---------------------------------------------------------------------


def content_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _verification(
    session: AsyncSession,
    status: DocumentStatus,
    claims: list[GeneratedClaim],
    **document: uuid.UUID,
) -> Verification:
    reports = await verification.reports_for(session, **document)
    latest = reports[0] if reports else None
    live = [c for c in claims if c.status != ClaimStatus.REMOVED]
    unverified = [
        UnverifiedClaim(text=c.claim_text, status=c.status.value)
        for c in live
        if c.status != ClaimStatus.VERIFIED
    ]
    if latest is not None:
        known = {u.text for u in unverified}
        unverified += [
            UnverifiedClaim(text=r.claim_text, status=r.verification_status.value, reason=r.reason)
            for r in latest.claims
            if not r.approved and r.claim_text not in known
        ]
    ok = (
        status in USABLE
        and latest is not None
        and latest.outcome == VerificationOutcome.APPROVED
        and not unverified
    )
    return Verification(
        document_status=status,
        verified=ok,
        outcome=latest.outcome if latest else None,
        verifier=latest.verifier if latest else None,
        checked_at=latest.created_at if latest else None,
        counts={k.value: v for k, v in latest.counts.items()} if latest else {},
        claims_verified=sum(1 for c in live if c.status == ClaimStatus.VERIFIED),
        unverified=unverified,
    )


async def _claims(session: AsyncSession, **where: uuid.UUID) -> list[GeneratedClaim]:
    column, value = next(iter(where.items()))
    rows = await session.scalars(
        select(GeneratedClaim)
        .where(getattr(GeneratedClaim, column) == value)
        .order_by(GeneratedClaim.section, GeneratedClaim.position)
    )
    return list(rows)


async def _document(
    session: AsyncSession, doc: TailoredResume | CoverLetter | None
) -> DocumentReview | None:
    if doc is None:
        return None
    key = "tailored_resume_id" if isinstance(doc, TailoredResume) else "cover_letter_id"
    claims = await _claims(session, **{key: doc.id})
    return DocumentReview(
        id=doc.id,
        version=doc.version,
        status=doc.status,
        content=doc.content,
        verification=await _verification(session, doc.status, claims, **{key: doc.id}),
    )


def _issues(
    a: Application,
    personal: PersonalReview,
    job: JobReview,
    resume: DocumentReview | None,
    letter: DocumentReview | None,
    answers: list[AnswerReview],
    unanswered: list[str],
) -> list[Issue]:
    issues: list[Issue] = []

    def add(section: str, severity: Literal["blocker", "warning"], message: str) -> None:
        issues.append(Issue(section=section, severity=severity, message=message))

    if a.status not in PREPARED and a.approval_state != ApprovalState.SUBMITTED:
        add("status", "blocker", "Move the application to “Application prepared” first.")
    if not personal.full_name.strip():
        add("personal", "blocker", "Your profile has no name.")
    if personal.email_source == "account":
        add(
            "personal",
            "warning",
            f"No contact email in your profile; your account email ({personal.email}) is used.",
        )
    if not personal.phone:
        add("personal", "warning", "No phone number in your profile.")
    if not personal.location:
        add("personal", "warning", "No location in your profile.")
    if not job.url:
        add("job", "warning", "No application link: you'll need to find where to apply.")
    if resume is None:
        add("resume", "blocker", "Attach a tailored resume.")
    elif not resume.verification.verified:
        add(
            "resume",
            "blocker",
            "The resume has claims that aren't verified against your evidence: re-verify, "
            "edit or regenerate it.",
        )
    if letter is None:
        add("cover_letter", "warning", "No cover letter attached (optional).")
    elif not letter.verification.verified:
        add(
            "cover_letter",
            "blocker",
            "The cover letter has claims that aren't verified against your evidence: fix, "
            "regenerate or detach it.",
        )
    for answer in answers:
        if not answer.verification.verified:
            add(
                "answers",
                "blocker",
                f"Your answer to “{answer.question}” has unverified claims.",
            )
        elif not answer.approved:
            add("answers", "blocker", f"Approve your answer to “{answer.question}”.")
    for question in unanswered:
        add("answers", "warning", f"No answer yet to “{question}”; it won't be included.")
    return issues


async def build(
    session: AsyncSession, user: User, a: Application
) -> tuple[ReviewPackage, dict[str, Any]]:
    """The review package and the content snapshot it hashes."""
    profile = await session.get(CandidateProfile, a.candidate_profile_id)
    assert profile is not None  # noqa: S101 - the FK guarantees it
    job = JobReview(
        id=a.job.id,
        title=a.job.title,
        company=a.job.company_name,
        location=a.job.location,
        url=a.application_url or a.job.url,
    )
    personal = PersonalReview(
        full_name=profile.full_name,
        email=profile.contact_email or user.email,
        email_source="profile" if profile.contact_email else "account",
        phone=profile.phone,
        location=profile.location,
        linkedin=profile.linkedin_url,
    )
    resume = await _document(session, a.tailored_resume)
    letter = await _document(session, a.cover_letter)
    rows = await session.scalars(
        select(ApplicationAnswer)
        .where(
            ApplicationAnswer.candidate_profile_id == a.candidate_profile_id,
            ApplicationAnswer.job_id == a.job_id,
        )
        .order_by(ApplicationAnswer.position, ApplicationAnswer.created_at)
    )
    answers: list[AnswerReview] = []
    unanswered: list[str] = []
    for row in rows:
        sentences = _sentences(row)
        if not sentences:
            unanswered.append(row.question)
            continue
        claims = await _claims(session, application_answer_id=row.id)
        answers.append(
            AnswerReview(
                id=row.id,
                question=row.question,
                answer=" ".join(s.text for s in sentences),
                status=row.status,
                approved=row.status == DocumentStatus.APPROVED,
                verification=await _verification(
                    session, row.status, claims, application_answer_id=row.id
                ),
            )
        )
    snapshot: dict[str, Any] = {
        "job": job.model_dump(mode="json", exclude={"url"}),
        "destination": job.url,
        "personal": personal.model_dump(mode="json"),
        "resume": {"id": str(resume.id), "version": resume.version, "content": resume.content}
        if resume
        else None,
        "cover_letter": {"id": str(letter.id), "version": letter.version, "content": letter.content}
        if letter
        else None,
        "answers": [{"id": str(x.id), "question": x.question, "answer": x.answer} for x in answers],
    }
    issues = _issues(a, personal, job, resume, letter, answers, unanswered)
    current = content_hash(snapshot)
    history, events = await _history(session, a)
    blockers = [i.message for i in issues if i.severity == "blocker"]
    submit_blockers = _submit_blockers(a, current, blockers)
    approval = None
    if a.approved_at is not None and a.approved_content_hash is not None:
        latest = next((h for h in history if h.decision == ApprovalDecision.APPROVED), None)
        reviewer = await session.get(User, a.approved_by_id) if a.approved_by_id else None
        approval = CurrentApproval(
            reviewer=reviewer.email if reviewer else None,
            approved_at=a.approved_at,
            content_hash=a.approved_content_hash,
            version=latest.version if latest else None,
        )
    state = a.approval_state
    package = ReviewPackage(
        application_id=a.id,
        status=a.status,
        approval_state=state,
        job=job,
        personal=personal,
        resume=resume,
        cover_letter=letter,
        answers=answers,
        unanswered=unanswered,
        issues=issues,
        content_hash=current,
        approval=approval,
        submitted_at=a.submitted_at,
        can_request_review=state in (ApprovalState.DRAFT, ApprovalState.REJECTED),
        can_approve=state == ApprovalState.READY_FOR_REVIEW and not blockers,
        can_submit=not submit_blockers,
        submit_blockers=submit_blockers,
        history=history,
        events=events,
    )
    return package, snapshot


def _submit_blockers(a: Application, current: str, blockers: list[str]) -> list[str]:
    if a.approval_state == ApprovalState.SUBMITTED:
        return ["Already submitted."]
    reasons: list[str] = []
    if a.approval_state != ApprovalState.APPROVED:
        reasons.append("You haven't approved this application.")
    elif a.approved_content_hash != current:
        reasons.append("It changed after you approved it: review and approve it again.")
    return reasons + blockers


async def _history(
    session: AsyncSession, a: Application
) -> tuple[list[ApprovalRecord], list[AuditEventOut]]:
    approvals = await session.execute(
        select(ApplicationApproval, User.email)
        .join(User, User.id == ApplicationApproval.reviewer_id)
        .where(ApplicationApproval.application_id == a.id)
        .order_by(ApplicationApproval.version.desc())
    )
    events = await session.execute(
        select(ApplicationAuditEvent, User.email)
        .outerjoin(User, User.id == ApplicationAuditEvent.user_id)
        .where(ApplicationAuditEvent.application_id == a.id)
        .order_by(ApplicationAuditEvent.created_at.desc(), ApplicationAuditEvent.id)
    )
    return (
        [
            ApprovalRecord(
                version=r.version,
                decision=r.decision,
                reviewer=email,
                at=r.created_at,
                content_hash=r.content_hash,
                note=r.note,
            )
            for r, email in approvals.all()
        ],
        [
            AuditEventOut(
                id=e.id,
                at=e.created_at,
                actor=e.actor,
                user=email,
                action=e.action,
                message=e.message,
                detail=e.detail,
            )
            for e, email in events.all()
        ],
    )


# --- Recording --------------------------------------------------------------------------------


async def audit(
    session: AsyncSession,
    a: Application,
    action: str,
    message: str,
    *,
    user: User | None,
    actor: StatusActor = StatusActor.USER,
    **detail: object,
) -> None:
    """Append an audit event, strictly after the application's previous one."""
    at = _now()
    last = await session.scalar(
        select(func.max(ApplicationAuditEvent.created_at)).where(
            ApplicationAuditEvent.application_id == a.id
        )
    )
    if last is not None and at <= last:
        at = last + timedelta(microseconds=1)
    session.add(
        ApplicationAuditEvent(
            application_id=a.id,
            actor=actor,
            user_id=user.id if user else None,
            action=action,
            message=message,
            detail=detail,
            created_at=at,
        )
    )
    await session.flush()


async def record_decision(
    session: AsyncSession,
    a: Application,
    reviewer: User,
    decision: ApprovalDecision,
    snapshot: dict[str, Any],
    note: str | None,
) -> int:
    version = (
        await session.scalar(
            select(func.coalesce(func.max(ApplicationApproval.version), 0)).where(
                ApplicationApproval.application_id == a.id
            )
        )
        or 0
    ) + 1
    session.add(
        ApplicationApproval(
            application_id=a.id,
            reviewer_id=reviewer.id,
            decision=decision,
            version=version,
            content_hash=content_hash(snapshot),
            content=snapshot,
            note=note,
        )
    )
    return version


def clear_approval(a: Application, to: ApprovalState) -> None:
    a.approval_state = to
    a.approved_at = a.approved_by_id = a.approved_content_hash = None


async def _approved_snapshot(session: AsyncSession, a: Application) -> dict[str, Any] | None:
    row = await session.scalar(
        select(ApplicationApproval)
        .where(
            ApplicationApproval.application_id == a.id,
            ApplicationApproval.decision == ApprovalDecision.APPROVED,
            ApplicationApproval.content_hash == a.approved_content_hash,
        )
        .order_by(ApplicationApproval.version.desc())
        .limit(1)
    )
    return row.content if row else None


async def invalidate_if_changed(
    session: AsyncSession, user: User | None, a: Application, snapshot: dict[str, Any]
) -> list[str]:
    """Withdraw an approval whose content changed. Returns the sections that changed."""
    if a.approval_state != ApprovalState.APPROVED:
        return []
    current = content_hash(snapshot)
    if a.approved_content_hash == current:
        return []
    approved = await _approved_snapshot(session, a) or {}
    changed = [k for k in SECTION_LABELS if approved.get(k) != snapshot.get(k)] or ["content"]
    previous = a.approved_content_hash
    clear_approval(a, ApprovalState.READY_FOR_REVIEW)
    await audit(
        session,
        a,
        "approval_invalidated",
        "Your approval was withdrawn because the application changed after you approved it ("
        + ", ".join(SECTION_LABELS.get(c, c) for c in changed)
        + "). Review and approve it again.",
        user=None,
        actor=StatusActor.SYSTEM,
        changed=changed,
        approved_hash=previous,
        current_hash=current,
    )
    return changed


_DECISION_FIELDS = [
    "status",
    "approval_state",
    "approved_at",
    "approved_by_id",
    "approved_content_hash",
    "submitted_at",
]


async def lock(session: AsyncSession, a: Application) -> None:
    """Serialize decisions about one application (approve, reject, status changes, assisted
    starts and submissions) with a row lock held until the transaction commits."""
    await session.execute(select(Application.id).where(Application.id == a.id).with_for_update())
    # Decide on the state as of the lock, not as first read (relationships stay loaded).
    await session.refresh(a, _DECISION_FIELDS)


async def ensure_not_submitting(session: AsyncSession, a: Application) -> None:
    """Refuse (409) while browser assistance is submitting this application: the outcome,
    and the approval it relies on, must be recorded before anything else changes."""
    submitting = await session.scalar(
        select(func.count())
        .select_from(ApplicationRun)
        .where(ApplicationRun.application_id == a.id, ApplicationRun.status == RunStatus.SUBMITTING)
    )
    if submitting:
        raise ConflictError(
            "CareerPilot is submitting this application right now. Wait for it to finish."
        )


async def reverify_documents(
    session: AsyncSession,
    user: User,
    a: Application,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> None:
    """Verify the resume and cover letter again against the candidate's current evidence,
    so approval and submission never rely on a report from before the evidence changed
    (answers are re-verified by their own approval). A claim that no longer passes is
    recorded in a new report, which the review then shows as a blocker; nothing is fixed."""
    if a.tailored_resume is not None:
        await resumes.reverify(session, user, a.tailored_resume.id, embedder, llm, settings)
    if a.cover_letter is not None:
        await letters.reverify(session, user, a.cover_letter.id, embedder, llm, settings)


async def ensure_can_submit(
    session: AsyncSession,
    user: User,
    a: Application,
    *,
    actor: StatusActor = StatusActor.USER,
) -> str:
    """Refuse (409, audited) unless the candidate approved exactly the current content and
    nothing blocks it. Returns the approved content hash."""
    package, snapshot = await build(session, user, a)
    changed = await invalidate_if_changed(session, user, a, snapshot)
    reasons = package.submit_blockers
    if changed:
        reasons = [
            "It changed after you approved it ("
            + ", ".join(SECTION_LABELS.get(c, c) for c in changed)
            + "): review and approve it again."
        ] + [r for r in reasons if not r.startswith("It changed")]
    if reasons:
        await audit(
            session,
            a,
            "submission_blocked",
            "Submission refused: " + " ".join(reasons),
            user=user,
            actor=actor,
            reasons=reasons,
        )
        await session.commit()
        raise ConflictError("Can't submit: " + " ".join(reasons))
    return package.content_hash


async def mark_submitted(
    session: AsyncSession,
    user: User,
    a: Application,
    *,
    actor: StatusActor,
    message: str,
    **detail: object,
) -> None:
    a.approval_state = ApprovalState.SUBMITTED
    await audit(
        session,
        a,
        "submitted",
        message,
        user=user,
        actor=actor,
        approved_hash=a.approved_content_hash,
        **detail,
    )
