"""Refreshing, listing and acting on recommendations."""

import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.applications.models import (
    Application,
    ApplicationStatus,
    ApplicationStatusHistory,
    StatusActor,
)
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.discovery import filters
from app.discovery import service as discovery
from app.discovery.access import AccessDenied
from app.discovery.models import JobSearchQuery, NormalizedJob
from app.discovery.provider import ProviderError
from app.discovery.registry import ProviderRegistry
from app.discovery.schemas import SourceOut
from app.jobs.models import Job, JobSource
from app.matching.engine.facts import load_facts
from app.profiles import service as profiles
from app.recommendations import pipeline
from app.recommendations.models import JobRecommendation, RecommendationStatus
from app.recommendations.schemas import (
    ActionResultOut,
    ApplicationRef,
    Explanation,
    RecommendationListOut,
    RecommendationOut,
)
from app.users.models import User

MAX_POSTINGS = 30  # postings analyzed per refresh (newest first, after preference filters)
MAX_ROLE_QUERIES = 3
View = Literal["recommended", "saved", "ignored", "filtered_out"]


def _visible_in(row: JobRecommendation, view: View) -> bool:
    if view == "ignored":
        return row.status == RecommendationStatus.IGNORED
    if row.status == RecommendationStatus.IGNORED:
        return False
    if view == "saved":
        return row.status == RecommendationStatus.SAVED
    if view == "filtered_out":
        return not row.eligible
    return row.eligible


async def _discover(
    registry: ProviderRegistry, queries: list[JobSearchQuery]
) -> tuple[list[NormalizedJob], list[str]]:
    postings: dict[tuple[str, str], NormalizedJob] = {}
    errors: list[str] = []
    for provider in registry.enabled():
        for query in queries:
            try:
                found = [filters.enrich(j) for j in await provider.search_jobs(query)]
            except AccessDenied as exc:
                errors.append(f"{provider.display_name}: {exc}")
                break  # a refusal ends this source for the refresh: no retries
            except ProviderError as exc:
                errors.append(f"{provider.display_name} is unavailable: {exc}")
                break
            for job in filters.apply(found, query):
                postings.setdefault((job.source, job.source_identifier), job)
    newest = sorted(
        postings.values(), key=lambda j: (j.posted_date or date.min, j.title), reverse=True
    )
    return newest[:MAX_POSTINGS], errors


async def refresh(
    session: AsyncSession, user: User, registry: ProviderRegistry, embedder: EmbeddingProvider
) -> RecommendationListOut:
    """Run the pipeline over newly discovered postings and store the explained results.

    Saved and ignored choices are kept; ignored postings stay hidden and aren't recomputed.
    """
    profile = await profiles.get_profile(session, user)
    facts = await load_facts(session, profile.id)
    queries = [JobSearchQuery(role=r) for r in profile.preferred_roles[:MAX_ROLE_QUERIES]]
    postings, errors = await _discover(registry, queries or [JobSearchQuery()])
    rows = {
        (r.source, r.source_identifier): r
        for r in await session.scalars(
            select(JobRecommendation).where(JobRecommendation.candidate_profile_id == profile.id)
        )
    }
    today, now = datetime.now(UTC).date(), datetime.now(UTC)
    seen: set[tuple[str, str]] = set()
    for posting in postings:
        key = (posting.source, posting.source_identifier)
        seen.add(key)
        row = rows.get(key)
        if row is not None and row.status == RecommendationStatus.IGNORED:
            continue
        evaluation = await pipeline.evaluate(session, profile.id, posting, facts, embedder)
        exclusions, concerns = pipeline.eligibility(evaluation, profile, facts, today)
        explanation = pipeline.explain(evaluation, profile)
        scores = evaluation.scores
        if row is None:
            row = JobRecommendation(
                candidate_profile_id=profile.id,
                source=posting.source,
                source_identifier=posting.source_identifier,
            )
            session.add(row)
            rows[key] = row
        row.posting = posting.model_dump(mode="json")
        row.eligible, row.exclusions, row.concerns = not exclusions, exclusions, concerns
        row.explanation = explanation.model_dump(mode="json")
        row.required_coverage = scores.required_coverage if scores else None
        row.overall_coverage = scores.overall if scores else None
        row.inputs_fingerprint, row.computed_at = facts.fingerprint, now

    # Postings that disappeared are dropped unless the candidate acted on them.
    for key, row in list(rows.items()):
        if key not in seen and row.status == RecommendationStatus.NEW and row.job_id is None:
            await session.delete(row)
            del rows[key]

    # Rank eligible postings by what the explanation shows: required coverage, then
    # preferences met, then matched skills; ties keep the newest first (stable sorts).
    ranked = sorted(
        (r for r in rows.values() if r.eligible),
        key=lambda r: r.posting.get("posted_date") or "",
        reverse=True,
    )
    ranked.sort(
        key=lambda r: (
            -(r.required_coverage or 0.0),
            -len(r.explanation["preference_fit"]),
            -len(r.explanation["matched_skills"]),
        )
    )
    for n, row in enumerate(ranked, start=1):
        row.rank = n
    for row in rows.values():
        if not row.eligible:
            row.rank = None
    await session.commit()
    return await list_recommendations(session, user, registry, "recommended", errors)


async def _applications(
    session: AsyncSession, profile_id: uuid.UUID, job_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ApplicationRef]:
    if not job_ids:
        return {}
    rows = await session.scalars(
        select(Application).where(
            Application.candidate_profile_id == profile_id, Application.job_id.in_(job_ids)
        )
    )
    return {a.job_id: ApplicationRef(id=a.id, status=a.status) for a in rows}


def _out(
    row: JobRecommendation, fingerprint: str, applications: dict[uuid.UUID, ApplicationRef]
) -> RecommendationOut:
    posting = NormalizedJob.model_validate(row.posting)
    return RecommendationOut(
        id=row.id,
        source=row.source,
        source_identifier=row.source_identifier,
        title=posting.title,
        company=posting.company,
        location=posting.location,
        url=posting.url,
        work_mode=posting.work_mode,
        employment_type=posting.employment_type,
        experience_level=posting.experience_level,
        posted_date=posting.posted_date,
        deadline=posting.deadline,
        status=row.status,
        eligible=row.eligible,
        exclusions=row.exclusions,
        concerns=row.concerns,
        explanation=Explanation.model_validate(row.explanation),
        required_coverage=row.required_coverage,
        overall_coverage=row.overall_coverage,
        rank=row.rank,
        computed_at=row.computed_at,
        is_stale=row.inputs_fingerprint != fingerprint,
        job_id=row.job_id,
        application=applications.get(row.job_id) if row.job_id else None,
    )


async def list_recommendations(
    session: AsyncSession,
    user: User,
    registry: ProviderRegistry,
    view: View = "recommended",
    errors: list[str] | None = None,
) -> RecommendationListOut:
    profile = await profiles.get_profile(session, user)
    facts = await load_facts(session, profile.id)
    rows = list(
        await session.scalars(
            select(JobRecommendation)
            .where(JobRecommendation.candidate_profile_id == profile.id)
            .execution_options(populate_existing=True)
        )
    )
    shown = sorted(
        (r for r in rows if _visible_in(r, view)),
        key=lambda r: (r.rank is None, r.rank or 0, r.posting.get("title", "")),
    )
    applications = await _applications(session, profile.id, [r.job_id for r in shown if r.job_id])
    views: tuple[View, ...] = ("recommended", "saved", "ignored", "filtered_out")
    return RecommendationListOut(
        recommendations=[_out(r, facts.fingerprint, applications) for r in shown],
        counts={v: sum(1 for r in rows if _visible_in(r, v)) for v in views},
        refreshed_at=max((r.computed_at for r in rows), default=None),
        errors=errors or [],
        sources=[SourceOut(**asdict(s)) for s in registry.statuses()],
    )


# --- Actions ------------------------------------------------------------------------------


async def _owned(session: AsyncSession, user: User, rec_id: uuid.UUID) -> JobRecommendation:
    profile = await profiles.get_profile(session, user)
    row = await session.scalar(
        select(JobRecommendation).where(
            JobRecommendation.id == rec_id, JobRecommendation.candidate_profile_id == profile.id
        )
    )
    if row is None:
        raise NotFoundError("Recommendation not found.")
    return row


async def _result(
    session: AsyncSession,
    user: User,
    row: JobRecommendation,
    application_id: uuid.UUID | None = None,
) -> ActionResultOut:
    facts = await load_facts(session, row.candidate_profile_id)
    await session.refresh(row)
    applications = await _applications(
        session, row.candidate_profile_id, [row.job_id] if row.job_id else []
    )
    return ActionResultOut(
        recommendation=_out(row, facts.fingerprint, applications),
        job_id=row.job_id,
        application_id=application_id,
    )


async def set_status(
    session: AsyncSession, user: User, rec_id: uuid.UUID, status: RecommendationStatus
) -> ActionResultOut:
    """Save, ignore, or restore (back to new) a recommendation."""
    row = await _owned(session, user, rec_id)
    row.status = status
    await session.commit()
    return await _result(session, user, row)


async def _ensure_job(
    session: AsyncSession,
    user: User,
    row: JobRecommendation,
    registry: ProviderRegistry,
    settings: Settings,
    llm: LLMProvider | None,
) -> uuid.UUID:
    """The candidate's job for this posting, importing it (with full analysis) if needed."""
    if row.job_id is not None and await session.get(Job, row.job_id) is not None:
        return row.job_id
    provider = registry.get(row.source)
    job_source = provider.job_source if provider is not None else JobSource.OTHER
    posting = NormalizedJob.model_validate(row.posting)
    imported = await discovery.import_posting(session, user, posting, job_source, settings, llm)
    row = await _owned(session, user, row.id)
    row.job_id = imported.job_id
    await session.commit()
    return imported.job_id


async def analyze(
    session: AsyncSession,
    user: User,
    rec_id: uuid.UUID,
    registry: ProviderRegistry,
    settings: Settings,
    llm: LLMProvider | None,
) -> ActionResultOut:
    """Import the posting as a job (full analysis), ready to match and tailor for."""
    row = await _owned(session, user, rec_id)
    await _ensure_job(session, user, row, registry, settings, llm)
    return await _result(session, user, await _owned(session, user, rec_id))


async def start_application(
    session: AsyncSession,
    user: User,
    rec_id: uuid.UUID,
    registry: ProviderRegistry,
    settings: Settings,
    llm: LLMProvider | None,
) -> ActionResultOut:
    """Start a draft application for the job. Nothing is submitted: every later step needs
    the candidate's explicit approval."""
    row = await _owned(session, user, rec_id)
    job_id = await _ensure_job(session, user, row, registry, settings, llm)
    row = await _owned(session, user, rec_id)
    application = await session.scalar(
        select(Application).where(
            Application.candidate_profile_id == row.candidate_profile_id,
            Application.job_id == job_id,
        )
    )
    if application is None:
        application = Application(
            candidate_profile_id=row.candidate_profile_id,
            job_id=job_id,
            status=ApplicationStatus.DRAFT,
            application_url=row.posting.get("url"),
            notes="Started from a job recommendation. Nothing is submitted without your approval.",
        )
        application.status_history.append(
            ApplicationStatusHistory(
                from_status=None,
                to_status=ApplicationStatus.DRAFT,
                actor=StatusActor.USER,
                note="Started from a job recommendation.",
            )
        )
        session.add(application)
    if row.status == RecommendationStatus.NEW:
        row.status = RecommendationStatus.SAVED
    await session.commit()
    return await _result(session, user, row, application.id)
