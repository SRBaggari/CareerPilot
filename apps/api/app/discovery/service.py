"""Discovery across providers, and import into CareerPilot's job pipeline.

Imported postings go through the same job analysis as a pasted description, so matching,
resumes, cover letters and answers work on them with no discovery-specific code.
"""

import logging
import uuid
from dataclasses import asdict

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMProvider
from app.core.config import Settings
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.discovery import filters
from app.discovery.access import AccessDenied
from app.discovery.models import JobSearchQuery, NormalizedJob
from app.discovery.provider import ProviderError
from app.discovery.registry import ProviderRegistry
from app.discovery.schemas import (
    DiscoveredJobOut,
    DiscoveryResultsOut,
    ImportResultOut,
    SourceOut,
)
from app.jobs import service as jobs
from app.jobs.models import Job, JobSource
from app.jobs.schemas import JobAnalyzeIn
from app.users.models import User

logger = logging.getLogger(__name__)


def sources(registry: ProviderRegistry) -> list[SourceOut]:
    return [SourceOut(**asdict(s)) for s in registry.statuses()]


async def _imported(
    session: AsyncSession, user: User, found: list[NormalizedJob]
) -> dict[tuple[str, str], uuid.UUID]:
    if not found:
        return {}
    keys = [(j.source, j.source_identifier) for j in found]
    rows = await session.execute(
        select(Job.source_name, Job.external_id, Job.id).where(
            Job.created_by_user_id == user.id,
            tuple_(Job.source_name, Job.external_id).in_(keys),
        )
    )
    return {(name, ext): job_id for name, ext, job_id in rows.all() if name and ext}


async def search(
    session: AsyncSession, user: User, query: JobSearchQuery, registry: ProviderRegistry
) -> DiscoveryResultsOut:
    providers = registry.enabled()
    if query.source is not None:
        providers = [p for p in providers if p.name == query.source]
        if not providers:
            raise NotFoundError(f"The job source '{query.source}' isn't enabled.")
    found: list[NormalizedJob] = []
    errors: list[str] = []
    for provider in providers:
        try:
            found += [filters.enrich(j) for j in await provider.search_jobs(query)]
        except AccessDenied as exc:
            errors.append(f"{provider.display_name}: {exc}")
        except ProviderError as exc:
            errors.append(f"{provider.display_name} is unavailable: {exc}")
    # The same posting from two sources (same URL) is shown once.
    unique: dict[str, NormalizedJob] = {}
    for job in found:
        unique.setdefault(job.url or f"{job.source}:{job.source_identifier}", job)
    matching = filters.apply(list(unique.values()), query)
    start = (query.page - 1) * query.page_size
    page = matching[start : start + query.page_size]
    imported = await _imported(session, user, page)
    return DiscoveryResultsOut(
        jobs=[
            DiscoveredJobOut(
                **job.model_dump(),
                matched_skills=filters.matched_skills(job, query.skills),
                imported_job_id=imported.get((job.source, job.source_identifier)),
            )
            for job in page
        ],
        total=len(matching),
        page=query.page,
        page_size=query.page_size,
        sources=sources(registry),
        errors=errors,
    )


async def _fetch(registry: ProviderRegistry, source: str, identifier: str) -> NormalizedJob:
    provider = registry.get(source)
    if provider is None:
        raise NotFoundError(f"The job source '{source}' isn't enabled.")
    try:
        job = await provider.get_job(identifier)
    except AccessDenied as exc:
        raise ServiceUnavailableError(f"{provider.display_name}: {exc}") from exc
    except ProviderError as exc:
        # Provider errors can carry URLs (and credentials in them): log, don't echo.
        logger.warning("Job source %s failed: %s", provider.name, exc)
        raise ServiceUnavailableError(
            f"{provider.display_name} is unavailable right now. Try again later."
        ) from exc
    if job is None:
        raise NotFoundError("That posting wasn't found at the source.")
    return filters.enrich(job)


async def get(
    session: AsyncSession, user: User, registry: ProviderRegistry, source: str, identifier: str
) -> DiscoveredJobOut:
    job = await _fetch(registry, source, identifier)
    imported = await _imported(session, user, [job])
    return DiscoveredJobOut(
        **job.model_dump(), matched_skills=[], imported_job_id=imported.get((source, identifier))
    )


async def import_posting(
    session: AsyncSession,
    user: User,
    posting: NormalizedJob,
    job_source: JobSource,
    settings: Settings,
    llm: LLMProvider | None,
) -> ImportResultOut:
    """Import a normalized posting as one of the user's jobs (once), analyzed like a pasted
    description. The source's structured fields take precedence over extracted ones."""
    existing = (await _imported(session, user, [posting])).get(
        (posting.source, posting.source_identifier)
    )
    if existing is not None:
        return ImportResultOut(job_id=existing, created=False)
    payload = JobAnalyzeIn(
        description=posting.description,
        source_url=posting.url,
        title=posting.title,
        company_name=posting.company,
        location=posting.location,
    )
    created = await jobs.analyze_job(
        session,
        user,
        payload,
        settings,
        llm,
        provenance=jobs.Provenance(
            source=job_source,
            source_name=posting.source,
            external_id=posting.source_identifier,
            workplace_type=posting.work_mode,
            employment_type=posting.employment_type,
            posted_date=posting.posted_date,
            deadline=posting.deadline,
        ),
    )
    return ImportResultOut(job_id=created.id, created=True)


async def import_job(
    session: AsyncSession,
    user: User,
    registry: ProviderRegistry,
    source: str,
    identifier: str,
    settings: Settings,
    llm: LLMProvider | None,
) -> ImportResultOut:
    """Fetch a posting from its source and import it."""
    posting = await _fetch(registry, source, identifier)
    provider = registry.get(source)
    assert provider is not None  # noqa: S101 - _fetch checked it
    return await import_posting(session, user, posting, provider.job_source, settings, llm)
