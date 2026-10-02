"""Assisted application runs: prepare, pause for review, submit only on explicit confirmation.

Preconditions, all enforced here:
- The application is approved by the candidate and not yet submitted.
- Its resume (and cover letter, if attached) and every answer used are APPROVED.
- The destination site has an adapter; otherwise the run stops and explains.
- Submission needs ``confirm: true`` and the hash of the review the candidate saw; the
  browser then only submits if the refilled form reads back to that same review.

Every step is recorded in the run's append-only audit log.
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.applications import approval
from app.applications import service as applications
from app.applications.models import (
    Application,
    ApplicationStatus,
    ApprovalState,
    FollowUpStatus,
    StatusActor,
)
from app.automation import runner
from app.automation.adapters.base import adapter_for, host_of
from app.automation.models import ApplicationRun, AuditActor, AutomationAuditEvent, RunStatus
from app.automation.plan import ApprovedAnswer, Document, Materials, Problem, split_name
from app.automation.review import Review
from app.automation.schemas import AuditEventOut, RunOut
from app.core.config import Settings
from app.core.errors import ConflictError, FieldValidationError, NotFoundError
from app.documents.answers.service import _sentences
from app.documents.cover_letter import render as letter_render
from app.documents.cover_letter import service as letters
from app.documents.models import ApplicationAnswer, DocumentStatus
from app.documents.resume import render as resume_render
from app.documents.resume import service as resumes
from app.profiles import service as profiles
from app.users.models import User

logger = logging.getLogger(__name__)

OPEN = (RunStatus.NEEDS_INPUT, RunStatus.AWAITING_REVIEW)
# A submission still "submitting" after this long was interrupted (a crash or restart while
# the browser ran). The runner's own time limits are far shorter.
SUBMITTING_STALE_AFTER = timedelta(minutes=10)
MAYBE_SENT = (
    "The submission was interrupted, so CareerPilot can't tell whether the employer received "
    "it. Check the employer's site or your email before applying again."
)


def _now() -> datetime:
    return datetime.now(UTC)


def _event(
    run: ApplicationRun,
    actor: AuditActor,
    action: str,
    message: str,
    at: datetime | None = None,
    **detail: object,
) -> None:
    """Append to the audit log, timestamped when it happened and strictly in order."""
    at = at or _now()
    last = max((e.created_at for e in run.events if e.created_at), default=None)
    if last is not None and at <= last:
        at = last + timedelta(microseconds=1)
    run.events.append(
        AutomationAuditEvent(
            actor=actor, action=action, message=message, detail=detail, created_at=at
        )
    )


# --- Loading ----------------------------------------------------------------------------------


async def _owned_run(session: AsyncSession, user: User, run_id: uuid.UUID) -> ApplicationRun:
    profile = await profiles.get_profile(session, user)
    run = await session.scalar(
        select(ApplicationRun)
        .join(Application, Application.id == ApplicationRun.application_id)
        .where(ApplicationRun.id == run_id, Application.candidate_profile_id == profile.id)
        .options(selectinload(ApplicationRun.events))
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise NotFoundError("Assisted application not found.")
    return run


async def _check_application(session: AsyncSession, user: User, a: Application) -> None:
    """Only applications approved by the candidate, unchanged since approval, are filled."""
    if a.submitted_at is not None:
        raise ConflictError("This application is already marked as submitted.")
    if a.approval_state == ApprovalState.APPROVED:
        _, snapshot = await approval.build(session, user, a)
        if await approval.invalidate_if_changed(session, user, a, snapshot):
            await session.commit()
            raise ConflictError(
                "The application changed after you approved it. Review and approve it again "
                "before CareerPilot fills it."
            )
    if (
        a.approval_state != ApprovalState.APPROVED
        or a.status != ApplicationStatus.AWAITING_APPROVAL
    ):
        raise ConflictError(
            "Approve the application first: CareerPilot only fills applications you approved."
        )


def _stem(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "candidate"


async def _materials(session: AsyncSession, user: User, a: Application) -> Materials:
    """The approved materials, rendered exactly as they will be uploaded."""
    profile = await profiles.get_profile(session, user)
    resume, letter = a.tailored_resume, a.cover_letter
    if resume is None or resume.status != DocumentStatus.APPROVED or resume.approved_at is None:
        raise ConflictError("The application's resume isn't approved.")
    if letter is not None and (
        letter.status != DocumentStatus.APPROVED or letter.approved_at is None
    ):
        raise ConflictError("The application's cover letter isn't approved.")
    first, last = split_name(profile.full_name)
    stem = _stem(profile.full_name)
    resume_doc = Document(
        kind="resume",
        version=resume.version,
        file_name=f"{stem}-resume-v{resume.version}.pdf",
        data=resume_render.to_pdf(resumes.as_content(resume), created=resume.approved_at),
    )
    letter_doc = None
    if letter is not None and letter.approved_at is not None:
        letter_doc = Document(
            kind="cover_letter",
            version=letter.version,
            file_name=f"{stem}-cover-letter-v{letter.version}.pdf",
            data=letter_render.to_pdf(
                letters.as_content(letter), letter.approved_at.date(), created=letter.approved_at
            ),
        )
    rows = await session.scalars(
        select(ApplicationAnswer)
        .where(
            ApplicationAnswer.candidate_profile_id == a.candidate_profile_id,
            ApplicationAnswer.job_id == a.job_id,
            ApplicationAnswer.status == DocumentStatus.APPROVED,
        )
        .order_by(ApplicationAnswer.position)
    )
    answers = [
        ApprovedAnswer(
            answer_id=row.id,
            question=row.question,
            text=" ".join(s.text for s in _sentences(row)),
        )
        for row in rows
        if _sentences(row)
    ]
    return Materials(
        personal={
            "first_name": first,
            "last_name": last,
            "email": profile.contact_email or user.email,
            "phone": profile.phone,
            "location": profile.location,
            "linkedin": profile.linkedin_url,
        },
        resume=resume_doc,
        cover_letter=letter_doc,
        answers=answers,
    )


# --- Output -----------------------------------------------------------------------------------


def _out(run: ApplicationRun) -> RunOut:
    return RunOut(
        id=run.id,
        application_id=run.application_id,
        status=run.status,
        destination_url=run.destination_url,
        destination_host=host_of(run.destination_url),
        adapter=run.adapter,
        inputs=run.inputs,
        review=Review.model_validate(run.review) if run.review else None,
        review_hash=run.review_hash,
        problems=[Problem.model_validate(p) for p in run.problems],
        stop_reason=run.stop_reason,
        prepared_at=run.prepared_at,
        confirmed_at=run.confirmed_at,
        submitted_at=run.submitted_at,
        confirmation_reference=run.confirmation_reference,
        nothing_submitted=run.status != RunStatus.SUBMITTED
        and not any(e.action == "submit_clicked" for e in run.events),
        created_at=run.created_at,
        updated_at=run.updated_at,
        events=[
            AuditEventOut(
                id=e.id,
                at=e.created_at,
                actor=e.actor,
                action=e.action,
                message=e.message,
                detail=e.detail,
            )
            for e in sorted(run.events, key=lambda e: (e.created_at, e.id))
        ],
    )


async def _reload(session: AsyncSession, user: User, run_id: uuid.UUID) -> RunOut:
    return _out(await _owned_run(session, user, run_id))


async def list_runs(session: AsyncSession, user: User, application_id: uuid.UUID) -> list[RunOut]:
    a = await applications._owned(session, user, application_id)
    runs = await session.scalars(
        select(ApplicationRun)
        .where(ApplicationRun.application_id == a.id)
        .options(selectinload(ApplicationRun.events))
        .order_by(ApplicationRun.created_at.desc())
    )
    return [_out(r) for r in runs]


async def get(session: AsyncSession, user: User, run_id: uuid.UUID) -> RunOut:
    return await _reload(session, user, run_id)


# --- Prepare ----------------------------------------------------------------------------------


def _record(run: ApplicationRun, outcome: runner.Outcome) -> None:
    for e in outcome.events:
        _event(run, e.actor, e.action, e.message, e.at, **e.detail)


async def _prepare(
    session: AsyncSession, user: User, run: ApplicationRun, a: Application, settings: Settings
) -> None:
    """Fill in a fresh browser and pause. Never submits."""
    run.review, run.review_hash, run.problems, run.stop_reason = None, None, [], None
    run.status = RunStatus.PREPARING
    adapter = adapter_for(run.destination_url, settings)
    if adapter is None:
        run.status = RunStatus.STOPPED
        run.stop_reason = (
            f"{host_of(run.destination_url)} isn't a supported application site, so "
            "CareerPilot won't try to fill it. Apply on the site yourself; your approved "
            "resume, cover letter and answers are ready to download."
        )
        _event(run, AuditActor.SYSTEM, "unsupported_site", run.stop_reason)
        await session.commit()
        return
    run.adapter = adapter.name
    materials = await _materials(session, user, a)
    await session.commit()  # the run is visible (and audited) while the browser works
    outcome = await runner.prepare(
        run.destination_url, adapter, materials, dict(run.inputs), settings
    )
    _record(run, outcome)
    if outcome.stop_reason is not None:
        run.status = RunStatus.FAILED if outcome.failed else RunStatus.STOPPED
        run.stop_reason = outcome.stop_reason
    elif outcome.problems:
        run.status = RunStatus.NEEDS_INPUT
        run.problems = [p.model_dump(mode="json") for p in outcome.problems]
    else:
        assert outcome.review is not None  # noqa: S101 - no stop and no problems
        run.status = RunStatus.AWAITING_REVIEW
        run.review = outcome.review.model_dump(mode="json")
        run.review_hash = outcome.review_hash
        run.prepared_at = _now()
    await session.commit()


async def start(
    session: AsyncSession,
    user: User,
    application_id: uuid.UUID,
    inputs: dict[str, str],
    settings: Settings,
) -> RunOut:
    a = await applications._owned(session, user, application_id)
    await approval.lock(session, a)
    await _settle_interrupted(session, a.id)
    await approval.ensure_not_submitting(session, a)
    await _check_application(session, user, a)
    url = a.application_url or a.job.url
    if not url:
        raise ConflictError("Add the application page's URL to the application first.")
    if urlparse(url).scheme not in ("http", "https"):
        raise FieldValidationError("application_url", "The application URL must be http(s).")
    earlier = await session.scalars(
        select(ApplicationRun)
        .where(ApplicationRun.application_id == a.id, ApplicationRun.status.in_(OPEN))
        .options(selectinload(ApplicationRun.events))
    )
    for old in earlier:
        old.status = RunStatus.CANCELLED
        _event(old, AuditActor.SYSTEM, "superseded", "Replaced by a new assisted application.")
    run = ApplicationRun(application_id=a.id, destination_url=url, inputs=_clean(inputs))
    run.events = []
    session.add(run)
    _event(
        run,
        AuditActor.USER,
        "started",
        "You asked CareerPilot to fill this application for your review.",
        destination=url,
    )
    await _prepare(session, user, run, a, settings)
    return await _reload(session, user, run.id)


async def _settle_interrupted(session: AsyncSession, application_id: uuid.UUID) -> None:
    """Mark submissions that were interrupted long ago as failed, saying they may have been
    sent, so they neither block the application forever nor read as "not submitted"."""
    stale = await session.scalars(
        select(ApplicationRun)
        .where(
            ApplicationRun.application_id == application_id,
            ApplicationRun.status == RunStatus.SUBMITTING,
            ApplicationRun.confirmed_at < _now() - SUBMITTING_STALE_AFTER,
        )
        .options(selectinload(ApplicationRun.events))
    )
    for run in stale:
        _interrupted(run)


def _interrupted(run: ApplicationRun) -> None:
    run.status, run.stop_reason = RunStatus.FAILED, MAYBE_SENT
    _event(run, AuditActor.SYSTEM, "interrupted", MAYBE_SENT)


def _clean(inputs: dict[str, str]) -> dict[str, str]:
    return {k[:100]: v.strip()[:2000] for k, v in inputs.items() if v and v.strip()}


async def provide_inputs(
    session: AsyncSession,
    user: User,
    run_id: uuid.UUID,
    inputs: dict[str, str],
    settings: Settings,
) -> RunOut:
    run = await _owned_run(session, user, run_id)
    if run.status not in OPEN:
        raise ConflictError("This assisted application is no longer open; start a new one.")
    a = await applications._owned(session, user, run.application_id)
    await _check_application(session, user, a)
    run.inputs = {**run.inputs, **_clean(inputs)}
    _event(
        run,
        AuditActor.USER,
        "provided_inputs",
        "You provided values for fields CareerPilot can't fill from your records.",
        fields=sorted(inputs),
    )
    await _prepare(session, user, run, a, settings)
    return await _reload(session, user, run.id)


# --- Submit -----------------------------------------------------------------------------------


async def submit(
    session: AsyncSession,
    user: User,
    run_id: uuid.UUID,
    review_hash: str,
    confirm: bool,
    settings: Settings,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
) -> RunOut:
    if not confirm:
        raise FieldValidationError(
            "confirm", "Confirm that you want to submit exactly what the review shows."
        )
    run = await _owned_run(session, user, run_id)
    if run.status != RunStatus.AWAITING_REVIEW or run.review_hash is None:
        raise ConflictError("This assisted application isn't waiting for your review.")
    a = await applications._owned(session, user, run.application_id)
    # Verified against the evidence as it is now (commits a new report); then locked.
    await approval.reverify_documents(session, user, a, embedder, llm, settings)
    await approval.lock(session, a)
    await session.refresh(run, with_for_update=True)  # one submission at a time
    if run.status != RunStatus.AWAITING_REVIEW or run.review_hash is None:
        raise ConflictError("This assisted application isn't waiting for your review.")
    if review_hash != run.review_hash:
        raise ConflictError(
            "The review changed since you looked at it. Review it again before submitting."
        )
    await _check_application(session, user, a)
    # Approved, unchanged since approval, nothing missing or unverified (409, audited).
    await approval.ensure_can_submit(session, user, a)
    adapter = adapter_for(run.destination_url, settings)
    if adapter is None or adapter.name != run.adapter:
        raise ConflictError("The destination site is no longer supported.")
    materials = await _materials(session, user, a)
    run.status, run.confirmed_at = RunStatus.SUBMITTING, _now()
    _event(
        run,
        AuditActor.USER,
        "confirmed",
        "You reviewed the application and confirmed submitting exactly what it shows.",
        review_hash=review_hash,
    )
    await session.commit()

    try:
        outcome = await runner.submit(
            run.destination_url, adapter, materials, dict(run.inputs), review_hash, settings
        )
    except Exception:
        # The form may already be on its way: never report "not submitted" or allow a quiet
        # retry. (A crash that skips this is settled by the next start after a timeout.)
        logger.exception("Assisted submission %s was interrupted", run_id)
        run = await _owned_run(session, user, run_id)
        _interrupted(run)
        await session.commit()
        return await _reload(session, user, run_id)
    run = await _owned_run(session, user, run_id)
    _record(run, outcome)
    if outcome.confirmation_reference is not None:
        now = _now()
        run.status, run.submitted_at = RunStatus.SUBMITTED, now
        run.confirmation_reference = outcome.confirmation_reference
        a = await applications._owned(session, user, run.application_id)
        a.submitted_at = now
        applications._history(
            a,
            ApplicationStatus.SUBMITTED,
            StatusActor.AUTOMATION,
            f"Submitted by CareerPilot on {host_of(run.destination_url)} after your explicit "
            f"confirmation (reference {outcome.confirmation_reference}).",
        )
        await approval.mark_submitted(
            session,
            user,
            a,
            actor=StatusActor.AUTOMATION,
            message=f"Submitted by CareerPilot on {host_of(run.destination_url)} after your "
            "explicit confirmation.",
            reference=outcome.confirmation_reference,
            run_id=str(run.id),
        )
        if not any(f.status == FollowUpStatus.PENDING for f in a.follow_ups):
            applications._add_reminder(
                a, "Check in on your application", now + applications.CHECK_IN_AFTER
            )
    else:
        run.status = RunStatus.FAILED if outcome.failed else RunStatus.STOPPED
        run.stop_reason = outcome.stop_reason
    await session.commit()
    return await _reload(session, user, run_id)


async def cancel(session: AsyncSession, user: User, run_id: uuid.UUID) -> RunOut:
    run = await _owned_run(session, user, run_id)
    if run.status not in (*OPEN, RunStatus.PREPARING):
        raise ConflictError("Only an open assisted application can be cancelled.")
    run.status = RunStatus.CANCELLED
    _event(run, AuditActor.USER, "cancelled", "You cancelled it. Nothing was submitted.")
    await session.commit()
    return await _reload(session, user, run_id)
