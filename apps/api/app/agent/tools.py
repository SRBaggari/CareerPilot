"""The agents' explicit tools, and the facts the state machine decides from.

Each tool is a plain async function that calls one existing CareerPilot service: the same
code paths, checks and verification as the rest of the app. Tools are idempotent (they
skip work that is already done), so resuming a run never duplicates documents. No tool
approves or submits anything: those are human actions the agent waits for.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.machine import Facts, Stage
from app.agent.models import ActionStatus, AgentName, AgentRun
from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.applications import approval, review
from app.applications import service as applications
from app.applications.models import (
    PRE_SUBMISSION,
    Application,
    ApplicationStatus,
    ApprovalState,
    FollowUpStatus,
)
from app.applications.schemas import ApplicationCreate, ApplicationUpdate, StatusChange
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.discovery import service as discovery
from app.discovery.registry import ProviderRegistry
from app.documents.answers import service as answers
from app.documents.answers.schemas import QuestionsIn
from app.documents.cover_letter import service as letters
from app.documents.models import (
    ApplicationAnswer,
    CoverLetter,
    DocumentStatus,
    QuestionType,
    TailoredResume,
)
from app.documents.resume import service as resumes
from app.jobs.models import Job, RequirementImportance
from app.matching import service as matching
from app.matching.models import MatchStatus
from app.matching.schemas import MatchReportOut
from app.profiles import service as profiles
from app.profiles.models import CandidateEvidence
from app.users.models import User

USABLE = (DocumentStatus.VERIFIED, DocumentStatus.APPROVED)


@dataclass
class Deps:
    settings: Settings
    embedder: EmbeddingProvider
    llm: LLMProvider | None
    registry: ProviderRegistry


@dataclass
class Ctx:
    session: AsyncSession
    user: User
    run: AgentRun
    deps: Deps


@dataclass
class Result:
    output: str  # a short summary of what happened
    status: ActionStatus = ActionStatus.SUCCEEDED


@dataclass(frozen=True)
class Tool:
    name: str
    agent: AgentName
    stage: Stage
    description: str
    run: Callable[[Ctx], Awaitable[Result]]
    inputs: Callable[[Ctx], str] = field(default=lambda ctx: "")


# --- Shared lookups ---------------------------------------------------------------------------


async def _job(ctx: Ctx) -> Job | None:
    if ctx.run.job_id is None:
        return None
    return await ctx.session.scalar(
        select(Job).where(Job.id == ctx.run.job_id, Job.created_by_user_id == ctx.user.id)
    )


async def _application(ctx: Ctx) -> Application | None:
    if ctx.run.job_id is None:
        return None
    found = await ctx.session.scalar(
        select(Application.id).where(
            Application.candidate_profile_id == ctx.run.candidate_profile_id,
            Application.job_id == ctx.run.job_id,
        )
    )
    if found is None:
        return None
    ctx.run.application_id = found
    return await applications._owned(ctx.session, ctx.user, found)


async def _latest(ctx: Ctx, model: type[TailoredResume] | type[CoverLetter]) -> object | None:
    return await ctx.session.scalar(
        select(model)
        .where(
            model.candidate_profile_id == ctx.run.candidate_profile_id,
            model.job_id == ctx.run.job_id,
        )
        .order_by(model.version.desc())
        .limit(1)
    )


def _job_label(job: Job | None) -> str:
    return f"{job.title} at {job.company_name}" if job else "the selected job"


# --- Candidate Profile Agent ------------------------------------------------------------------


async def check_profile(ctx: Ctx) -> Result:
    profile = await profiles.get_profile(ctx.session, ctx.user)
    evidence = await ctx.session.scalar(
        select(func.count())
        .select_from(CandidateEvidence)
        .where(
            CandidateEvidence.candidate_profile_id == profile.id,
            CandidateEvidence.confirmed_at.is_not(None),
        )
    )
    return Result(
        f"Profile of {profile.full_name or '(no name)'}: {evidence} confirmed evidence items.",
        ActionStatus.SUCCEEDED,
    )


# --- Job Discovery Agent ----------------------------------------------------------------------


async def select_job(ctx: Ctx) -> Result:
    goal = ctx.run.goal
    if ctx.run.job_id is not None:
        return Result(f"Job already selected: {_job_label(await _job(ctx))}.", ActionStatus.SKIPPED)
    if goal.get("job_id"):
        job = await ctx.session.scalar(
            select(Job).where(
                Job.id == uuid.UUID(goal["job_id"]), Job.created_by_user_id == ctx.user.id
            )
        )
        if job is None:
            raise NotFoundError("The job you chose wasn't found.")
        ctx.run.job_id = job.id
        return Result(f"Selected {_job_label(job)}.")
    imported = await discovery.import_job(
        ctx.session,
        ctx.user,
        ctx.deps.registry,
        goal["source"],
        goal["external_id"],
        ctx.deps.settings,
        ctx.deps.llm,
    )
    ctx.run.job_id = imported.job_id
    job = await _job(ctx)
    verb = "Imported" if imported.created else "Found the already imported"
    return Result(f"{verb} posting {goal['source']}:{goal['external_id']} as {_job_label(job)}.")


# --- Job Analysis Agent -----------------------------------------------------------------------


async def review_analysis(ctx: Ctx) -> Result:
    job = await _job(ctx)
    if job is None:
        raise NotFoundError("No job selected.")
    reqs = list(await job.awaitable_attrs.requirements)
    required = sum(1 for r in reqs if r.importance == RequirementImportance.REQUIRED)
    preferred = sum(1 for r in reqs if r.importance == RequirementImportance.PREFERRED)
    deadline = f", deadline {job.application_deadline}" if job.application_deadline else ""
    return Result(
        f"{_job_label(job)}: {required} required and {preferred} preferred requirements "
        f"(analyzed by {job.analyzer_name or 'unknown'}){deadline}."
    )


# --- Matching Agent ---------------------------------------------------------------------------


async def compute_match(ctx: Ctx) -> Result:
    assert ctx.run.job_id is not None  # noqa: S101 - entry check
    try:
        current = await matching.get_report(ctx.session, ctx.user, ctx.run.job_id)
        if not current.is_stale:
            return Result(_match_summary(current), ActionStatus.SKIPPED)
    except NotFoundError:
        pass
    report = await matching.compute_match(
        ctx.session,
        ctx.user,
        ctx.run.job_id,
        ctx.deps.embedder,
        ctx.deps.llm,
        ctx.deps.settings,
    )
    return Result(_match_summary(report))


def _match_summary(report: MatchReportOut) -> str:
    counts = ", ".join(f"{n} {s.value}" for s, n in report.status_counts.items() if n)
    return (
        f"Evidence coverage {report.scores.evidence_coverage:.0%} ({counts}); "
        f"{len(report.potentially_disqualifying)} potentially disqualifying."
    )


# --- Resume and Cover Letter Agents -----------------------------------------------------------


async def tailor_resume(ctx: Ctx) -> Result:
    assert ctx.run.job_id is not None  # noqa: S101 - entry check
    existing = await _latest(ctx, TailoredResume)
    if isinstance(existing, TailoredResume):
        return Result(
            f"Using tailored resume version {existing.version} ({existing.status.value}).",
            ActionStatus.SKIPPED,
        )
    d = ctx.deps
    out = await resumes.generate(
        ctx.session, ctx.user, ctx.run.job_id, d.embedder, d.llm, d.settings, d.llm, d.llm
    )
    return Result(f"Generated tailored resume version {out.version} ({out.status.value}).")


async def write_cover_letter(ctx: Ctx) -> Result:
    assert ctx.run.job_id is not None  # noqa: S101 - entry check
    if not ctx.run.goal.get("include_cover_letter", True):
        return Result("No cover letter requested.", ActionStatus.SKIPPED)
    existing = await _latest(ctx, CoverLetter)
    if isinstance(existing, CoverLetter):
        return Result(
            f"Using cover letter version {existing.version} ({existing.status.value}).",
            ActionStatus.SKIPPED,
        )
    d = ctx.deps
    out = await letters.generate(
        ctx.session, ctx.user, ctx.run.job_id, d.embedder, d.llm, d.settings, d.llm, d.llm
    )
    return Result(f"Generated cover letter version {out.version} ({out.status.value}).")


# --- Application Preparation Agent ------------------------------------------------------------


async def prepare_application(ctx: Ctx) -> Result:
    assert ctx.run.job_id is not None  # noqa: S101 - entry check
    done: list[str] = []
    out, created = await applications.create(
        ctx.session, ctx.user, ApplicationCreate(job_id=ctx.run.job_id)
    )
    ctx.run.application_id = out.id
    done.append("started tracking the application" if created else "found the application")
    a = await applications._owned(ctx.session, ctx.user, out.id)
    if a.approved_at is None and a.submitted_at is None:
        resume, letter = await _latest(ctx, TailoredResume), await _latest(ctx, CoverLetter)
        changes: dict[str, uuid.UUID] = {}
        if isinstance(resume, TailoredResume) and a.tailored_resume_id != resume.id:
            changes["tailored_resume_id"] = resume.id
        if (
            ctx.run.goal.get("include_cover_letter", True)
            and isinstance(letter, CoverLetter)
            and a.cover_letter_id != letter.id
        ):
            changes["cover_letter_id"] = letter.id
        if changes:
            await applications.update(
                ctx.session, ctx.user, out.id, ApplicationUpdate.model_validate(changes)
            )
            done.append("attached the latest documents")
    asked = set(
        await ctx.session.scalars(
            select(ApplicationAnswer.question).where(
                ApplicationAnswer.candidate_profile_id == ctx.run.candidate_profile_id,
                ApplicationAnswer.job_id == ctx.run.job_id,
            )
        )
    )
    new = [q for q in ctx.run.goal.get("questions", []) if q not in asked]
    if new:
        d = ctx.deps
        await answers.create(
            ctx.session,
            ctx.user,
            ctx.run.job_id,
            QuestionsIn(questions=new),
            d.embedder,
            d.llm,
            d.settings,
            d.llm,
            d.llm,
        )
        done.append(f"answered {len(new)} application question{'s' if len(new) != 1 else ''}")
    a = await applications._owned(ctx.session, ctx.user, out.id)
    if a.status in PRE_SUBMISSION[:3]:  # discovered, saved, analyzed
        await applications.change_status(
            ctx.session,
            ctx.user,
            out.id,
            StatusChange(
                status=ApplicationStatus.APPLICATION_PREPARED, note="Prepared by the agent."
            ),
        )
        done.append("moved it to Application prepared")
    return Result("; ".join(done).capitalize() + ".")


# --- Claim Verification Agent -----------------------------------------------------------------


async def check_claims(ctx: Ctx) -> Result:
    a = await _application(ctx)
    if a is None:
        raise NotFoundError("There is no application to verify.")
    package, _ = await approval.build(ctx.session, ctx.user, a)
    parts = []
    for name, doc in (("resume", package.resume), ("cover letter", package.cover_letter)):
        if doc is not None:
            v = doc.verification
            parts.append(
                f"{name}: {'verified' if v.verified else f'{len(v.unverified)} unverified'}"
            )
    verified = sum(1 for x in package.answers if x.verification.verified)
    parts.append(f"answers: {verified} of {len(package.answers)} verified")
    if package.unanswered:
        parts.append(f"{len(package.unanswered)} unanswered")
    return Result("; ".join(parts) + ".")


# --- Human Approval Agent (it waits for the human; it never approves) -------------------------


async def request_review(ctx: Ctx) -> Result:
    a = await _application(ctx)
    if a is None:
        raise NotFoundError("There is no application to review.")
    if a.approval_state not in (ApprovalState.DRAFT, ApprovalState.REJECTED):
        return Result(f"Approval state: {a.approval_state.value}.", ActionStatus.SKIPPED)
    await review.request_review(ctx.session, ctx.user, a.id)
    return Result("Marked the application ready for your review.")


async def check_approval(ctx: Ctx) -> Result:
    a = await _application(ctx)
    if a is None:
        raise NotFoundError("There is no application.")
    _, snapshot = await approval.build(ctx.session, ctx.user, a)
    changed = await approval.invalidate_if_changed(ctx.session, ctx.user, a, snapshot)
    if changed:
        return Result(f"Your approval was withdrawn: {', '.join(changed)} changed.")
    return Result(f"Approval state: {a.approval_state.value}.")


async def check_submission(ctx: Ctx) -> Result:
    a = await _application(ctx)
    if a is None:
        raise NotFoundError("There is no application.")
    if a.submitted_at is None:
        return Result("Not submitted yet.")
    return Result(f"Submitted on {a.submitted_at.date()}.")


# --- Application Tracking Agent ---------------------------------------------------------------


async def track_application(ctx: Ctx) -> Result:
    a = await _application(ctx)
    if a is None or a.submitted_at is None:
        raise NotFoundError("There is no submitted application to track.")
    pending = [f for f in a.follow_ups if f.status == FollowUpStatus.PENDING and f.due_at]
    if not pending and a.status == ApplicationStatus.SUBMITTED:
        applications._add_reminder(
            a,
            "Check in on your application",
            max(datetime.now(UTC), a.submitted_at + applications.CHECK_IN_AFTER),
        )
        await ctx.session.flush()
        return Result("Scheduled a check-in reminder.")
    due = min((f.due_at for f in pending if f.due_at), default=None)
    return Result(
        f"Status {a.status.value}" + (f"; next follow-up due {due.date()}." if due else "."),
        ActionStatus.SKIPPED,
    )


# --- The registry -----------------------------------------------------------------------------


def _goal_inputs(ctx: Ctx) -> str:
    g = ctx.run.goal
    return (
        f"job {g['job_id']}"
        if g.get("job_id")
        else f"posting {g.get('source')}:{g.get('external_id')}"
    )


def _job_inputs(ctx: Ctx) -> str:
    return f"job {ctx.run.job_id}"


def _app_inputs(ctx: Ctx) -> str:
    return f"application {ctx.run.application_id}" if ctx.run.application_id else _job_inputs(ctx)


A = AgentName
TOOLS: tuple[Tool, ...] = (
    Tool(
        "check_profile",
        A.CANDIDATE_PROFILE,
        Stage.DISCOVER,
        "Check the profile has a name and confirmed evidence.",
        check_profile,
        lambda ctx: f"profile {ctx.run.candidate_profile_id}",
    ),
    Tool(
        "select_job",
        A.JOB_DISCOVERY,
        Stage.DISCOVER,
        "Select the chosen job, or import the chosen posting from its source.",
        select_job,
        _goal_inputs,
    ),
    Tool(
        "review_analysis",
        A.JOB_ANALYSIS,
        Stage.ANALYZE,
        "Read the job's analyzed requirements and deadline.",
        review_analysis,
        _job_inputs,
    ),
    Tool(
        "compute_match",
        A.MATCHING,
        Stage.MATCH,
        "Match the job's requirements to verified evidence (reuses a current match).",
        compute_match,
        _job_inputs,
    ),
    Tool(
        "tailor_resume",
        A.RESUME,
        Stage.PREPARE,
        "Generate a tailored resume (reuses the latest version).",
        tailor_resume,
        _job_inputs,
    ),
    Tool(
        "write_cover_letter",
        A.COVER_LETTER,
        Stage.PREPARE,
        "Generate a cover letter if requested (reuses the latest version).",
        write_cover_letter,
        _job_inputs,
    ),
    Tool(
        "prepare_application",
        A.APPLICATION_PREPARATION,
        Stage.PREPARE,
        "Track the application, attach documents, answer the questions, mark it prepared.",
        prepare_application,
        lambda ctx: f"{_job_inputs(ctx)}, {len(ctx.run.goal.get('questions', []))} questions",
    ),
    Tool(
        "check_claims",
        A.CLAIM_VERIFICATION,
        Stage.VERIFY,
        "Collect the verification results of every document and answer.",
        check_claims,
        _app_inputs,
    ),
    Tool(
        "request_review",
        A.HUMAN_APPROVAL,
        Stage.REVIEW,
        "Mark the application ready for the candidate's review.",
        request_review,
        _app_inputs,
    ),
    Tool(
        "check_approval",
        A.HUMAN_APPROVAL,
        Stage.APPROVE,
        "Check the candidate approved the current content (never approves).",
        check_approval,
        _app_inputs,
    ),
    Tool(
        "check_submission",
        A.HUMAN_APPROVAL,
        Stage.SUBMIT,
        "Check the candidate submitted it (never submits).",
        check_submission,
        _app_inputs,
    ),
    Tool(
        "track_application",
        A.APPLICATION_TRACKING,
        Stage.TRACK,
        "Make sure a follow-up reminder is scheduled.",
        track_application,
        _app_inputs,
    ),
)
BY_STAGE: dict[Stage, list[Tool]] = {s: [t for t in TOOLS if t.stage == s] for s in Stage}


# --- Facts ------------------------------------------------------------------------------------


async def gather(ctx: Ctx) -> Facts:
    """The real state, read fresh from the database."""
    profile = await profiles.get_profile(ctx.session, ctx.user)
    missing: list[str] = []
    if not profile.full_name.strip():
        missing.append("Your name")
    evidence = await ctx.session.scalar(
        select(func.count())
        .select_from(CandidateEvidence)
        .where(
            CandidateEvidence.candidate_profile_id == profile.id,
            CandidateEvidence.confirmed_at.is_not(None),
        )
    )
    if not evidence:
        missing.append("Confirmed evidence: add or confirm your experience, projects or skills")
    job = await _job(ctx)
    facts: dict[str, object] = {
        "profile_missing": missing,
        "eligibility_confirmed": ctx.run.inputs.get("confirm_eligibility") is True,
    }
    if job is None:
        return Facts(**facts)  # type: ignore[arg-type]
    reqs = list(await job.awaitable_attrs.requirements)
    facts |= {
        "job_id": job.id,
        "requirements": sum(1 for r in reqs if r.importance != RequirementImportance.INFORMATIONAL),
        "deadline_passed": bool(
            job.application_deadline and job.application_deadline < datetime.now(UTC).date()
        ),
    }
    try:
        report = await matching.get_report(ctx.session, ctx.user, job.id)
    except NotFoundError:
        report = None
    if report is not None and not report.is_stale:
        facts |= {
            "matched": True,
            "disqualifying": [r.requirement for r in report.potentially_disqualifying],
            "unknown_required": [
                r.requirement
                for r in report.requirements
                if r.match_status == MatchStatus.UNKNOWN
                and r.importance == RequirementImportance.REQUIRED
            ],
        }
    a = await _application(ctx)
    if a is None:
        return Facts(**facts)  # type: ignore[arg-type]
    package, _ = await approval.build(ctx.session, ctx.user, a)
    unverified = [
        f"{label}: “{c.text}” ({c.status.replace('_', ' ')})"
        for label, doc in (("Resume", package.resume), ("Cover letter", package.cover_letter))
        if doc is not None
        for c in (doc.verification.unverified or [])
    ]
    for label, doc in (("Resume", package.resume), ("Cover letter", package.cover_letter)):
        if doc is not None and not doc.verification.verified and not doc.verification.unverified:
            unverified.append(f"{label}: not verified ({doc.status.value.replace('_', ' ')})")
    for x in package.answers:
        if not x.verification.verified:
            unverified.append(f"Answer to “{x.question}”: not verified")
    current = a.approval_state == ApprovalState.SUBMITTED or (
        a.approval_state == ApprovalState.APPROVED
        and a.approved_content_hash == package.content_hash
    )
    facts |= {
        "application_id": a.id,
        "resume_attached": a.tailored_resume_id is not None,
        "missing_fields": [
            i.message
            for i in package.issues
            if i.severity == "blocker" and i.section in ("personal", "status")
        ],
        "unverified": unverified,
        "unanswered": package.unanswered,
        "ambiguous": list(
            await ctx.session.scalars(
                select(ApplicationAnswer.question)
                .where(
                    ApplicationAnswer.candidate_profile_id == ctx.run.candidate_profile_id,
                    ApplicationAnswer.job_id == job.id,
                    ApplicationAnswer.question_type == QuestionType.OTHER,
                    ApplicationAnswer.status != DocumentStatus.APPROVED,
                )
                .order_by(ApplicationAnswer.position)
            )
        ),
        "review_state": a.approval_state.value,
        "unapproved_answers": [x.question for x in package.answers if not x.approved],
        "approval_current": current,
        "submitted": a.submitted_at is not None,
        "tracked": a.submitted_at is not None
        and (
            any(f.status == FollowUpStatus.PENDING for f in a.follow_ups)
            or a.status != ApplicationStatus.SUBMITTED
        ),
    }
    return Facts(**facts)  # type: ignore[arg-type]
