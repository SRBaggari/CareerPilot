"""The application tracker: lifecycle, approval, interviews, follow-up reminders, timeline.

CareerPilot never submits an application. "Submitted" records that the candidate submitted
it, and is only reachable after the candidate explicitly approved the application (the
database enforces this too).
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.applications import approval
from app.applications.models import (
    APPROVAL_REQUIRED_STATUSES,
    PRE_SUBMISSION,
    Application,
    ApplicationStatus,
    ApplicationStatusHistory,
    ApprovalState,
    FollowUp,
    FollowUpChannel,
    FollowUpStatus,
    Interview,
    InterviewStatus,
    StatusActor,
)
from app.applications.schemas import (
    AnswerRef,
    ApplicationCreate,
    ApplicationOut,
    ApplicationSummaryOut,
    ApplicationUpdate,
    DashboardOut,
    DocumentRef,
    FollowUpIn,
    FollowUpOut,
    FollowUpUpdate,
    InterviewIn,
    InterviewOut,
    InterviewUpdate,
    ReadinessItem,
    StatusChange,
    TimelineEvent,
)
from app.core.errors import ConflictError, FieldValidationError, NotFoundError
from app.documents.models import ApplicationAnswer, CoverLetter, DocumentStatus, TailoredResume
from app.jobs.models import Job
from app.profiles import service as profiles
from app.users.models import User

S = ApplicationStatus
CLOSED = (S.REJECTED, S.WITHDRAWN)
SUBMITTED_STAGES = APPROVAL_REQUIRED_STATUSES  # submitted, assessment, interview, offer
USABLE_DOCUMENT = (DocumentStatus.VERIFIED, DocumentStatus.APPROVED)
CHECK_IN_AFTER = timedelta(days=7)  # reminder to check in after submitting
THANK_YOU_AFTER = timedelta(days=1)  # reminder to thank interviewers
DUE_SOON = timedelta(days=7)
INTERVIEWS_AHEAD = timedelta(days=14)
LABELS = {
    S.DISCOVERED: "Discovered",
    S.SAVED: "Saved",
    S.ANALYZED: "Analyzed",
    S.APPLICATION_PREPARED: "Application prepared",
    S.AWAITING_APPROVAL: "Awaiting approval",
    S.SUBMITTED: "Submitted",
    S.ASSESSMENT: "Assessment",
    S.INTERVIEW: "Interview",
    S.OFFER: "Offer",
    S.REJECTED: "Rejected",
    S.WITHDRAWN: "Withdrawn",
}


def _now() -> datetime:
    return datetime.now(UTC)


# --- Loading ----------------------------------------------------------------------------------


def _with_details(statement: Select[Application]) -> Select[Application]:
    return statement.options(
        selectinload(Application.job),
        selectinload(Application.interviews),
        selectinload(Application.follow_ups),
        selectinload(Application.status_history),
        selectinload(Application.tailored_resume),
        selectinload(Application.cover_letter),
    ).execution_options(populate_existing=True)


async def _owned(session: AsyncSession, user: User, application_id: uuid.UUID) -> Application:
    profile = await profiles.get_profile(session, user)
    application = await session.scalar(
        _with_details(
            select(Application).where(
                Application.id == application_id,
                Application.candidate_profile_id == profile.id,
            )
        )
    )
    if application is None:
        raise NotFoundError("Application not found.")
    return application


async def _answers(
    session: AsyncSession, profile_id: uuid.UUID, job_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[ApplicationAnswer]]:
    rows = await session.scalars(
        select(ApplicationAnswer)
        .where(
            ApplicationAnswer.candidate_profile_id == profile_id,
            ApplicationAnswer.job_id.in_(list(job_ids)),
        )
        .order_by(ApplicationAnswer.position)
    )
    found: dict[uuid.UUID, list[ApplicationAnswer]] = {}
    for answer in rows:
        found.setdefault(answer.job_id, []).append(answer)
    return found


def _written(answers: list[ApplicationAnswer]) -> list[ApplicationAnswer]:
    """Answers with text (an empty draft isn't part of the application)."""
    return [a for a in answers if (a.answer or {}).get("sentences")]


# --- Lifecycle rules ----------------------------------------------------------------------------


def allowed_statuses(application: Application) -> list[ApplicationStatus]:
    """Where an application can move next.

    Before submission it moves freely between the preparation stages. Submission needs the
    candidate's approval first; later stages need the submission. Nothing moves back from
    submitted to preparation. Rejected and withdrawn can be reopened.
    """
    submitted = application.submitted_at is not None
    if submitted:
        options = [*SUBMITTED_STAGES, *CLOSED]
    else:
        options = [*PRE_SUBMISSION, S.WITHDRAWN]
        if application.approved_at is not None:
            options.append(S.SUBMITTED)
    return [s for s in options if s != application.status]


def readiness(
    application: Application, answers: list[ApplicationAnswer]
) -> tuple[list[ReadinessItem], list[str]]:
    """What the application includes, and what blocks approval."""
    items: list[ReadinessItem] = []
    resume = application.tailored_resume
    if resume is None:
        items.append(
            ReadinessItem(
                label="Tailored resume", ok=False, required=True, detail="Attach a tailored resume."
            )
        )
    else:
        ok = resume.status in USABLE_DOCUMENT
        items.append(
            ReadinessItem(
                label="Tailored resume",
                ok=ok,
                required=True,
                detail=f"Version {resume.version}, {resume.status.value.replace('_', ' ')}."
                if ok
                else "The attached resume failed verification: re-verify or regenerate it.",
            )
        )
    letter = application.cover_letter
    if letter is None:
        items.append(
            ReadinessItem(
                label="Cover letter", ok=True, required=False, detail="None attached (optional)."
            )
        )
    else:
        ok = letter.status in USABLE_DOCUMENT
        items.append(
            ReadinessItem(
                label="Cover letter",
                ok=ok,
                required=True,
                detail=f"Version {letter.version}, {letter.status.value.replace('_', ' ')}."
                if ok
                else "The attached cover letter failed verification: fix or detach it.",
            )
        )
    written = _written(answers)
    approved = [a for a in written if a.status == DocumentStatus.APPROVED]
    items.append(
        ReadinessItem(
            label="Application answers",
            ok=len(approved) == len(written),
            required=True,
            detail=f"{len(approved)} of {len(written)} approved."
            if written
            else "No application questions (optional).",
        )
    )
    blockers = [i.detail for i in items if i.required and not i.ok]
    if written and len(approved) < len(written):
        blockers[-1] = (
            f"Approve every application answer ({len(approved)} of {len(written)} approved)."
        )
    if application.status not in (S.APPLICATION_PREPARED, S.AWAITING_APPROVAL):
        blockers.insert(0, "Move the application to “Application prepared” first.")
    if application.approved_at is not None:
        blockers = ["Already approved."]
    return items, blockers


def _history(
    application: Application, to: ApplicationStatus, actor: StatusActor, note: str | None
) -> None:
    application.status_history.append(
        ApplicationStatusHistory(
            from_status=application.status if application.id else None,
            to_status=to,
            actor=actor,
            note=note,
        )
    )
    application.status = to


def _add_reminder(
    application: Application, subject: str, due: datetime, interview_id: uuid.UUID | None = None
) -> None:
    application.follow_ups.append(
        FollowUp(
            channel=FollowUpChannel.EMAIL,
            status=FollowUpStatus.PENDING,
            due_at=due,
            subject=subject,
            interview_id=interview_id,
            notes="Suggested by CareerPilot. It never sends anything for you.",
        )
    )


# --- Output -----------------------------------------------------------------------------------


def _document(doc: TailoredResume | CoverLetter | None, latest: int | None) -> DocumentRef | None:
    if doc is None:
        return None
    return DocumentRef(
        id=doc.id,
        version=doc.version,
        status=doc.status,
        created_at=doc.created_at,
        newer_version=latest if latest and latest > doc.version else None,
    )


def _interview_out(i: Interview, a: Application) -> InterviewOut:
    return InterviewOut(
        id=i.id,
        application_id=a.id,
        company=a.job.company_name,
        position=a.job.title,
        interview_type=i.interview_type,
        status=i.status,
        scheduled_at=i.scheduled_at,
        duration_minutes=i.duration_minutes,
        location=i.location,
        meeting_url=i.meeting_url,
        notes=i.notes,
    )


def _follow_up_out(f: FollowUp, a: Application, now: datetime) -> FollowUpOut:
    return FollowUpOut(
        id=f.id,
        application_id=a.id,
        company=a.job.company_name,
        position=a.job.title,
        interview_id=f.interview_id,
        channel=f.channel,
        status=f.status,
        due_at=f.due_at,
        completed_at=f.completed_at,
        subject=f.subject,
        notes=f.notes,
        overdue=f.status == FollowUpStatus.PENDING and f.due_at is not None and f.due_at < now,
    )


def _summary(
    a: Application, answers: list[ApplicationAnswer], now: datetime
) -> ApplicationSummaryOut:
    upcoming = [
        i.scheduled_at
        for i in a.interviews
        if i.status == InterviewStatus.SCHEDULED and i.scheduled_at and i.scheduled_at >= now
    ]
    pending = [f for f in a.follow_ups if f.status == FollowUpStatus.PENDING and f.due_at]
    written = _written(answers)
    return ApplicationSummaryOut(
        id=a.id,
        job_id=a.job_id,
        company=a.job.company_name,
        position=a.job.title,
        location=a.job.location,
        job_url=a.application_url or a.job.url,
        status=a.status,
        discovered_at=a.discovered_at,
        applied_at=a.submitted_at,
        approved_at=a.approved_at,
        approval_state=a.approval_state,
        updated_at=a.updated_at,
        next_interview_at=min(upcoming, default=None),
        next_follow_up_at=min((f.due_at for f in pending if f.due_at), default=None),
        overdue_follow_ups=sum(1 for f in pending if f.due_at and f.due_at < now),
        has_resume=a.tailored_resume_id is not None,
        has_cover_letter=a.cover_letter_id is not None,
        answers_approved=sum(1 for x in written if x.status == DocumentStatus.APPROVED),
        answers_total=len(written),
    )


def timeline(
    a: Application, answers: list[ApplicationAnswer], now: datetime
) -> list[TimelineEvent]:
    history = sorted(a.status_history, key=lambda h: h.created_at)
    first = history[0].to_status if history and history[0].from_status is None else None
    events = []
    if a.discovered_at and a.discovered_at < a.created_at:
        events.append(TimelineEvent(at=a.discovered_at, kind="created", title="Discovered"))
    events.append(
        TimelineEvent(
            at=a.created_at,
            kind="created",
            title=f"Started tracking as {LABELS[first]}" if first else "Started tracking",
            detail=f"{a.job.title} at {a.job.company_name}",
        )
    )
    for h in history:
        if h.from_status is None:
            continue  # shown as "Started tracking"
        who = {
            StatusActor.USER: "",
            StatusActor.SYSTEM: " (automatic)",
            StatusActor.AUTOMATION: " (automation)",
        }[h.actor]
        events.append(
            TimelineEvent(
                at=h.created_at,
                kind="status",
                title=f"Moved to {LABELS[h.to_status]}{who}",
                detail=h.note,
            )
        )
    if a.approved_at:
        events.append(TimelineEvent(at=a.approved_at, kind="approval", title="Approved by you"))
    if a.tailored_resume:
        events.append(
            TimelineEvent(
                at=a.tailored_resume.created_at,
                kind="document",
                title=f"Tailored resume v{a.tailored_resume.version} created",
            )
        )
    if a.cover_letter:
        events.append(
            TimelineEvent(
                at=a.cover_letter.created_at,
                kind="document",
                title=f"Cover letter v{a.cover_letter.version} created",
            )
        )
    for answer in answers:
        if answer.approved_at:
            events.append(
                TimelineEvent(
                    at=answer.approved_at,
                    kind="answer",
                    title="Answer approved",
                    detail=answer.question,
                )
            )
    for i in a.interviews:
        when = i.scheduled_at or i.created_at
        label = i.interview_type.value.replace("_", " ")
        events.append(
            TimelineEvent(
                at=when,
                kind="interview",
                upcoming=when > now and i.status == InterviewStatus.SCHEDULED,
                title=f"Interview ({label}): {i.status.value.replace('_', ' ')}",
                detail=i.location or i.meeting_url or i.notes,
            )
        )
    for f in a.follow_ups:
        if f.status == FollowUpStatus.DONE and f.completed_at:
            events.append(
                TimelineEvent(
                    at=f.completed_at, kind="follow_up", title=f"Followed up: {f.subject}"
                )
            )
        elif f.status == FollowUpStatus.PENDING and f.due_at:
            events.append(
                TimelineEvent(
                    at=f.due_at,
                    kind="follow_up",
                    upcoming=f.due_at > now,
                    title=f"Follow-up due: {f.subject}",
                    detail=f.notes,
                )
            )
    # Newest first; events at the same moment keep their logical order (later steps first).
    ordered = sorted(enumerate(events), key=lambda pair: (pair[1].at, pair[0]), reverse=True)
    return [event for _, event in ordered]


async def _latest_versions(session: AsyncSession, a: Application) -> tuple[int | None, int | None]:
    resume = await session.scalar(
        select(func.max(TailoredResume.version)).where(
            TailoredResume.candidate_profile_id == a.candidate_profile_id,
            TailoredResume.job_id == a.job_id,
        )
    )
    letter = await session.scalar(
        select(func.max(CoverLetter.version)).where(
            CoverLetter.candidate_profile_id == a.candidate_profile_id,
            CoverLetter.job_id == a.job_id,
        )
    )
    return resume, letter


async def _out(session: AsyncSession, user: User, application_id: uuid.UUID) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    now = _now()
    answers = (await _answers(session, a.candidate_profile_id, [a.job_id])).get(a.job_id, [])
    items, blockers = readiness(a, answers)
    latest_resume, latest_letter = await _latest_versions(session, a)
    return ApplicationOut(
        **_summary(a, answers, now).model_dump(),
        notes=a.notes,
        resume=_document(a.tailored_resume, latest_resume),
        cover_letter=_document(a.cover_letter, latest_letter),
        answers=[
            AnswerRef(
                id=x.id,
                question=x.question,
                status=x.status,
                approved=x.status == DocumentStatus.APPROVED,
            )
            for x in answers
        ],
        readiness=items,
        approval_blockers=blockers,
        allowed_statuses=allowed_statuses(a),
        interviews=[
            _interview_out(i, a)
            for i in sorted(a.interviews, key=lambda i: i.scheduled_at or i.created_at)
        ],
        follow_ups=[
            _follow_up_out(f, a, now)
            for f in sorted(a.follow_ups, key=lambda f: f.due_at or f.created_at)
        ],
        timeline=timeline(a, answers, now),
    )


# --- Queries ----------------------------------------------------------------------------------


async def list_applications(
    session: AsyncSession,
    user: User,
    *,
    statuses: list[ApplicationStatus] | None = None,
    q: str | None = None,
    follow_up_due: bool = False,
    sort: str = "updated",
) -> list[ApplicationSummaryOut]:
    """Applications, filtered by status and searched by company, position, location or
    notes. ``follow_up_due`` keeps those with a follow-up overdue or due within a week."""
    profile = await profiles.get_profile(session, user)
    statement = (
        select(Application)
        .join(Job, Job.id == Application.job_id)
        .where(Application.candidate_profile_id == profile.id)
    )
    if statuses:
        statement = statement.where(Application.status.in_(statuses))
    if q and q.strip():
        term = q.strip().replace("\\", "").replace("%", "").replace("_", "")
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                Job.company_name.ilike(pattern),
                Job.title.ilike(pattern),
                Job.location.ilike(pattern),
                Application.notes.ilike(pattern),
            )
        )
    rows = list(await session.scalars(_with_details(statement)))
    answers = await _answers(session, profile.id, {a.job_id for a in rows})
    now = _now()
    summaries = [_summary(a, answers.get(a.job_id, []), now) for a in rows]
    if follow_up_due:
        summaries = [
            s for s in summaries if s.next_follow_up_at and s.next_follow_up_at <= now + DUE_SOON
        ]
    keys = {
        "updated": lambda s: -s.updated_at.timestamp(),
        "company": lambda s: s.company.lower(),
        "discovered": lambda s: -(s.discovered_at or s.updated_at).timestamp(),
        "applied": lambda s: -(s.applied_at.timestamp() if s.applied_at else 0),
    }
    return sorted(summaries, key=keys.get(sort, keys["updated"]))


async def get(session: AsyncSession, user: User, application_id: uuid.UUID) -> ApplicationOut:
    return await _out(session, user, application_id)


async def dashboard(session: AsyncSession, user: User) -> DashboardOut:
    profile = await profiles.get_profile(session, user)
    rows = list(
        await session.scalars(
            _with_details(select(Application).where(Application.candidate_profile_id == profile.id))
        )
    )
    answers = await _answers(session, profile.id, {a.job_id for a in rows})
    now = _now()
    due = [
        _follow_up_out(f, a, now)
        for a in rows
        for f in a.follow_ups
        if f.status == FollowUpStatus.PENDING and f.due_at and f.due_at <= now + DUE_SOON
    ]
    interviews = [
        _interview_out(i, a)
        for a in rows
        for i in a.interviews
        if i.status == InterviewStatus.SCHEDULED
        and i.scheduled_at
        and now <= i.scheduled_at <= now + INTERVIEWS_AHEAD
    ]
    summaries = [_summary(a, answers.get(a.job_id, []), now) for a in rows]
    return DashboardOut(
        counts={s: sum(1 for a in rows if a.status == s) for s in ApplicationStatus},
        total=len(rows),
        active=sum(1 for a in rows if a.status not in CLOSED),
        follow_ups_due=sorted(due, key=lambda f: f.due_at or now),
        upcoming_interviews=sorted(interviews, key=lambda i: i.scheduled_at or now),
        recent=sorted(summaries, key=lambda s: s.updated_at, reverse=True)[:5],
    )


# --- Commands ---------------------------------------------------------------------------------


async def create(
    session: AsyncSession, user: User, payload: ApplicationCreate
) -> tuple[ApplicationOut, bool]:
    """Start tracking a job (once per job). Returns (application, created)."""
    profile = await profiles.get_profile(session, user)
    job = await session.scalar(
        select(Job).where(Job.id == payload.job_id, Job.created_by_user_id == user.id)
    )
    if job is None:
        raise NotFoundError("Job not found.")
    existing = await session.scalar(
        select(Application.id).where(
            Application.candidate_profile_id == profile.id, Application.job_id == job.id
        )
    )
    if existing is not None:
        return await _out(session, user, existing), False
    resume = await session.scalar(
        select(TailoredResume)
        .where(TailoredResume.candidate_profile_id == profile.id, TailoredResume.job_id == job.id)
        .order_by(TailoredResume.version.desc())
        .limit(1)
    )
    letter = await session.scalar(
        select(CoverLetter)
        .where(CoverLetter.candidate_profile_id == profile.id, CoverLetter.job_id == job.id)
        .order_by(CoverLetter.version.desc())
        .limit(1)
    )
    application = Application(
        candidate_profile_id=profile.id,
        job_id=job.id,
        status=payload.status,
        discovered_at=job.created_at,
        application_url=job.url,
        notes=payload.notes,
        tailored_resume_id=resume.id if resume else None,
        cover_letter_id=letter.id if letter else None,
    )
    application.status_history.append(
        ApplicationStatusHistory(
            from_status=None,
            to_status=payload.status,
            actor=StatusActor.USER,
            note="Started tracking this application.",
        )
    )
    session.add(application)
    await session.commit()
    return await _out(session, user, application.id), True


def _check_document(doc: TailoredResume | CoverLetter | None, a: Application, field: str) -> None:
    if doc is None or doc.candidate_profile_id != a.candidate_profile_id or doc.job_id != a.job_id:
        raise FieldValidationError(field, "That document isn't one of yours for this job.")


async def update(
    session: AsyncSession, user: User, application_id: uuid.UUID, payload: ApplicationUpdate
) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    changes = payload.model_dump(exclude_unset=True)
    if a.approved_at is not None and {"tailored_resume_id", "cover_letter_id"} & set(changes):
        raise ConflictError("The documents of an approved application can't be changed.")
    if changes.get("tailored_resume_id"):
        resume = await session.get(TailoredResume, changes["tailored_resume_id"])
        _check_document(resume, a, "tailored_resume_id")
    if changes.get("cover_letter_id"):
        letter = await session.get(CoverLetter, changes["cover_letter_id"])
        _check_document(letter, a, "cover_letter_id")

    for name, value in changes.items():
        setattr(a, name, value)
    if a.approval_state == ApprovalState.APPROVED:
        _, snapshot = await approval.build(session, user, a)
        await approval.invalidate_if_changed(session, user, a, snapshot)
    await session.commit()
    return await _out(session, user, application_id)


async def change_status(
    session: AsyncSession, user: User, application_id: uuid.UUID, payload: StatusChange
) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    target = payload.status
    if target == a.status:
        return await _out(session, user, application_id)
    if target == S.SUBMITTED and a.submitted_at is None:
        # Approved, unchanged since approval, nothing missing or unverified (409 otherwise).
        await approval.ensure_can_submit(session, user, a)
    if target not in allowed_statuses(a):
        if target in SUBMITTED_STAGES and a.approved_at is None:
            reason = (
                "Approve the application first: nothing counts as submitted without your approval."
            )
        elif target in SUBMITTED_STAGES:
            reason = "Mark the application as submitted first."
        else:
            reason = "A submitted application can't move back to preparation."
        raise ConflictError(f"Can't move to {LABELS[target]}. {reason}")
    if target == S.SUBMITTED and a.submitted_at is None:
        assert a.approved_at is not None  # noqa: S101 - allowed_statuses checked it
        on = payload.submitted_on or _now().date()
        submitted = datetime.combine(on, time(12, 0), UTC) if payload.submitted_on else _now()
        if submitted < a.approved_at:
            if on < a.approved_at.date():
                raise FieldValidationError(
                    "submitted_on", "The date you applied can't be before you approved it."
                )
            submitted = a.approved_at
        a.submitted_at = submitted
        await approval.mark_submitted(
            session, user, a, actor=StatusActor.USER, message="You recorded it as submitted."
        )
        if not any(f.status == FollowUpStatus.PENDING for f in a.follow_ups):
            _add_reminder(a, "Check in on your application", submitted + CHECK_IN_AFTER)
    _history(a, target, StatusActor.USER, payload.note)
    await session.commit()
    return await _out(session, user, application_id)


async def delete(session: AsyncSession, user: User, application_id: uuid.UUID) -> None:
    a = await _owned(session, user, application_id)
    await session.delete(a)  # history, interviews and follow-ups cascade
    await session.commit()


# --- Interviews and follow-ups ------------------------------------------------------------------


async def add_interview(
    session: AsyncSession, user: User, application_id: uuid.UUID, payload: InterviewIn
) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    if a.submitted_at is None:
        raise ConflictError("Record that you submitted the application before adding interviews.")
    a.interviews.append(Interview(**payload.model_dump()))
    if a.status in (S.SUBMITTED, S.ASSESSMENT):
        _history(a, S.INTERVIEW, StatusActor.SYSTEM, "An interview was added.")
    await session.commit()
    return await _out(session, user, application_id)


async def _interview(a: Application, interview_id: uuid.UUID) -> Interview:
    for interview in a.interviews:
        if interview.id == interview_id:
            return interview
    raise NotFoundError("Interview not found.")


async def update_interview(
    session: AsyncSession,
    user: User,
    application_id: uuid.UUID,
    interview_id: uuid.UUID,
    payload: InterviewUpdate,
) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    interview = await _interview(a, interview_id)
    changes = payload.model_dump(exclude_unset=True)
    for name, value in changes.items():
        setattr(interview, name, value)
    if changes.get("status") == InterviewStatus.COMPLETED and not any(
        f.interview_id == interview.id for f in a.follow_ups
    ):
        after = max(interview.scheduled_at or _now(), _now()) if interview.scheduled_at else _now()
        _add_reminder(a, "Send a thank-you note", after + THANK_YOU_AFTER, interview.id)
    await session.commit()
    return await _out(session, user, application_id)


async def delete_interview(
    session: AsyncSession, user: User, application_id: uuid.UUID, interview_id: uuid.UUID
) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    await session.delete(await _interview(a, interview_id))
    await session.commit()
    return await _out(session, user, application_id)


async def add_follow_up(
    session: AsyncSession, user: User, application_id: uuid.UUID, payload: FollowUpIn
) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    if payload.interview_id is not None:
        await _interview(a, payload.interview_id)
    a.follow_ups.append(FollowUp(**payload.model_dump(), status=FollowUpStatus.PENDING))
    await session.commit()
    return await _out(session, user, application_id)


def _follow_up(a: Application, follow_up_id: uuid.UUID) -> FollowUp:
    for follow_up in a.follow_ups:
        if follow_up.id == follow_up_id:
            return follow_up
    raise NotFoundError("Follow-up not found.")


async def update_follow_up(
    session: AsyncSession,
    user: User,
    application_id: uuid.UUID,
    follow_up_id: uuid.UUID,
    payload: FollowUpUpdate,
) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    follow_up = _follow_up(a, follow_up_id)
    changes = payload.model_dump(exclude_unset=True)
    for name, value in changes.items():
        setattr(follow_up, name, value)
    if "status" in changes:
        follow_up.completed_at = _now() if follow_up.status == FollowUpStatus.DONE else None
    await session.commit()
    return await _out(session, user, application_id)


async def delete_follow_up(
    session: AsyncSession, user: User, application_id: uuid.UUID, follow_up_id: uuid.UUID
) -> ApplicationOut:
    a = await _owned(session, user, application_id)
    await session.delete(_follow_up(a, follow_up_id))
    await session.commit()
    return await _out(session, user, application_id)
