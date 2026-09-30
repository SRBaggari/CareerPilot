"""Job analysis service: analyze pasted descriptions, store manual entries, manage jobs.

Jobs added by a user are visible only to that user (``created_by_user_id``).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.models import AIExecutionLog, AIExecutionStatus, AIOperation
from app.ai.provider import LLMError, LLMProvider
from app.applications.models import Application
from app.core.config import Settings
from app.core.errors import ConflictError, FieldErrors, NotFoundError
from app.documents.models import CoverLetter, TailoredResume
from app.jobs.analysis.extracted import ExtractedRequirement, JobExtraction
from app.jobs.analysis.grounding import ground
from app.jobs.analysis.heuristic import HeuristicJobAnalyzer
from app.jobs.analysis.llm import PROMPT_VERSION, LLMJobAnalyzer
from app.jobs.analysis.vocabulary import CATEGORY_BY_NAME
from app.jobs.models import (
    Job,
    JobInputMethod,
    JobRequirement,
    JobSource,
    RequirementImportance,
    RequirementType,
)
from app.jobs.schemas import (
    JobAnalyzeIn,
    JobManualIn,
    JobOut,
    JobSummaryOut,
    RequirementCounts,
    RequirementOut,
    SalaryOut,
)
from app.profiles.service import get_or_create_skill
from app.resumes.extraction import normalize_text
from app.users.models import User

MAX_WARNINGS = 50


# --- Output -------------------------------------------------------------------------------


def _counts(job: Job) -> RequirementCounts:
    by = {i: 0 for i in RequirementImportance}
    for requirement in job.requirements:
        by[requirement.importance] += 1
    return RequirementCounts(
        required=by[RequirementImportance.REQUIRED],
        preferred=by[RequirementImportance.PREFERRED],
        informational=by[RequirementImportance.INFORMATIONAL],
    )


def _summary_fields(job: Job) -> dict[str, object]:
    return {
        "id": job.id, "title": job.title, "company_name": job.company_name,
        "location": job.location, "workplace_type": job.workplace_type,
        "employment_type": job.employment_type,
        "application_deadline": job.application_deadline, "input_method": job.input_method,
        "requirement_counts": _counts(job), "created_at": job.created_at,
    }  # fmt: skip


def to_out(job: Job) -> JobOut:
    has_salary = any(v is not None for v in (job.salary_text, job.salary_min, job.salary_max))
    salary = SalaryOut(
        text=job.salary_text, minimum=job.salary_min, maximum=job.salary_max,
        currency=job.salary_currency, period=job.salary_period,
    ) if has_salary else None  # fmt: skip
    return JobOut(
        **_summary_fields(job),
        source_url=job.url,
        description=job.description,
        salary=salary,
        analyzer_name=job.analyzer_name,
        analysis_warnings=job.analysis_warnings,
        analyzed_at=job.analyzed_at,
        requirements=[
            RequirementOut(
                id=r.id, requirement_type=r.requirement_type, importance=r.importance,
                description=r.description, source_excerpt=r.source_excerpt,
                min_years=r.min_years, skill_id=r.skill_id,
            )
            for r in sorted(job.requirements, key=lambda r: r.sort_order)
        ],
    )  # fmt: skip


# --- Persistence --------------------------------------------------------------------------


async def _requirement_rows(
    session: AsyncSession, requirements: list[ExtractedRequirement]
) -> list[JobRequirement]:
    rows = []
    for order, r in enumerate(requirements):
        skill_id = None
        if r.requirement_type == RequirementType.TECHNOLOGY:
            skill = await get_or_create_skill(
                session, r.description[:100], CATEGORY_BY_NAME.get(r.description)
            )
            skill_id = skill.id
        rows.append(
            JobRequirement(
                requirement_type=r.requirement_type, importance=r.importance,
                description=r.description, source_excerpt=r.source_excerpt or None,
                min_years=r.min_years, skill_id=skill_id, sort_order=order,
            )
        )  # fmt: skip
    return rows


async def _get_owned(session: AsyncSession, user: User, job_id: uuid.UUID) -> Job:
    job = await session.scalar(
        select(Job)
        .where(Job.id == job_id, Job.created_by_user_id == user.id)
        .options(selectinload(Job.requirements))
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise NotFoundError("Job not found.")
    return job


# --- Analysis -----------------------------------------------------------------------------


async def _extract(
    session: AsyncSession, user: User, text: str, settings: Settings, llm: LLMProvider | None
) -> tuple[JobExtraction, str]:
    """Run the configured analyzer; the LLM path falls back to rule-based on failure."""
    warnings: list[str] = []
    if settings.job_analyzer != "heuristic" and llm is not None:
        analyzer = LLMJobAnalyzer(llm)
        log = AIExecutionLog(
            user_id=user.id, operation=AIOperation.JOB_ANALYSIS, provider=llm.name,
            model=llm.model, prompt_version=PROMPT_VERSION,
        )  # fmt: skip
        try:
            extraction, result = await analyzer.analyze(text)
        except LLMError as exc:
            log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
            session.add(log)
            warnings.append(f"AI analysis failed ({exc}); the rule-based analyzer was used.")
        else:
            log.status, log.model = AIExecutionStatus.SUCCESS, result.model
            log.input_tokens, log.output_tokens = result.input_tokens, result.output_tokens
            log.latency_ms = result.latency_ms
            session.add(log)
            return extraction, analyzer.name
    elif settings.job_analyzer == "llm":
        warnings.append(
            "The AI analyzer is not configured (set ANTHROPIC_API_KEY); the rule-based "
            "analyzer was used."
        )
    heuristic = HeuristicJobAnalyzer()
    extraction = heuristic.analyze(text)
    extraction.warnings[:0] = warnings
    return extraction, heuristic.name


async def analyze_job(
    session: AsyncSession,
    user: User,
    payload: JobAnalyzeIn,
    settings: Settings,
    llm: LLMProvider | None,
) -> JobOut:
    text = normalize_text(payload.description)
    raw, analyzer_name = await _extract(session, user, text, settings, llm)
    extraction = ground(raw, text)

    title = payload.title or extraction.title
    company = payload.company_name or extraction.company_name
    if not title or not company:
        missing = {}
        if not title:
            missing["title"] = "The job title wasn't found in the description. Please enter it."
        if not company:
            missing["company_name"] = (
                "The company wasn't found in the description. Please enter it."
            )
        await session.commit()  # an AI call that already happened is still logged (usage/cost)
        raise FieldErrors(missing)

    salary = extraction.salary
    job = Job(
        source=JobSource.MANUAL,
        url=payload.source_url,
        title=title[:300],
        company_name=company[:300],
        location=(payload.location or extraction.location or None),
        workplace_type=extraction.workplace_type,
        employment_type=extraction.employment_type,
        description=text,
        salary_min=salary.minimum if salary else None,
        salary_max=salary.maximum if salary else None,
        salary_currency=salary.currency if salary else None,
        salary_period=salary.period if salary else None,
        salary_text=salary.text if salary else None,
        application_deadline=extraction.application_deadline,
        created_by_user_id=user.id,
        input_method=JobInputMethod.PASTED_TEXT,
        analyzer_name=analyzer_name,
        analysis_warnings=[w[:300] for w in extraction.warnings][:MAX_WARNINGS],
        analyzed_at=datetime.now(UTC),
    )
    job.requirements = await _requirement_rows(session, extraction.requirements)
    session.add(job)
    await session.commit()
    return to_out(await _get_owned(session, user, job.id))


async def create_manual_job(session: AsyncSession, user: User, payload: JobManualIn) -> JobOut:
    requirements = [
        ExtractedRequirement(r.requirement_type, r.importance, r.description, "", r.min_years)
        for r in payload.requirements
    ]
    job = Job(
        source=JobSource.MANUAL,
        url=payload.source_url,
        title=payload.title,
        company_name=payload.company_name,
        location=payload.location,
        workplace_type=payload.workplace_type,
        employment_type=payload.employment_type,
        description=payload.description,
        salary_min=payload.salary_min,
        salary_max=payload.salary_max,
        salary_currency=payload.salary_currency,
        salary_period=payload.salary_period,
        salary_text=payload.salary_text,
        application_deadline=payload.application_deadline,
        created_by_user_id=user.id,
        input_method=JobInputMethod.MANUAL_ENTRY,
    )
    job.requirements = await _requirement_rows(session, requirements)
    session.add(job)
    await session.commit()
    return to_out(await _get_owned(session, user, job.id))


async def list_jobs(session: AsyncSession, user: User) -> list[JobSummaryOut]:
    jobs = await session.scalars(
        select(Job)
        .where(Job.created_by_user_id == user.id)
        .options(selectinload(Job.requirements))
        .order_by(Job.created_at.desc())
    )
    return [JobSummaryOut(**_summary_fields(job)) for job in jobs]


async def get_job(session: AsyncSession, user: User, job_id: uuid.UUID) -> JobOut:
    return to_out(await _get_owned(session, user, job_id))


async def delete_job(session: AsyncSession, user: User, job_id: uuid.UUID) -> None:
    job = await _get_owned(session, user, job_id)
    in_use = await session.scalar(
        select(
            exists().where(TailoredResume.job_id == job.id)
            | exists().where(CoverLetter.job_id == job.id)
            | exists().where(Application.job_id == job.id)
        )
    )
    if in_use:
        raise ConflictError(
            "This job has generated documents or an application; it can't be deleted."
        )
    await session.delete(job)
    await session.commit()
