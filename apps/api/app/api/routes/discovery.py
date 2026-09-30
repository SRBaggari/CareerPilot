"""Job discovery API: search enabled job sources, view a posting, import it as a job.

Sources are provider adapters for official APIs or published feeds (plus the development
mock). Nothing here scrapes sites or bypasses access controls.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMProvider
from app.api.routes.jobs import get_job_llm
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.discovery import service
from app.discovery.models import JobSearchQuery
from app.discovery.registry import ProviderRegistry, build_registry
from app.discovery.schemas import (
    DiscoveredJobOut,
    DiscoveryResultsOut,
    ImportResultOut,
    SourceOut,
)
from app.profiles.models import EmploymentType, ExperienceLevel
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1/discovery", tags=["job discovery"])


def get_job_sources(settings: Settings = Depends(get_settings)) -> ProviderRegistry:
    return build_registry(settings)


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    user: User = Depends(get_current_user),
    registry: ProviderRegistry = Depends(get_job_sources),
) -> list[SourceOut]:
    """Configured job sources, whether each may be used, and why not."""
    return service.sources(registry)


@router.get("/jobs", response_model=DiscoveryResultsOut)
async def search_jobs(
    role: Annotated[str | None, Query(max_length=200)] = None,
    location: Annotated[str | None, Query(max_length=200)] = None,
    remote: bool | None = None,
    employment_type: Annotated[list[EmploymentType] | None, Query()] = None,
    skills: Annotated[list[str] | None, Query(max_length=20)] = None,
    experience_level: Annotated[list[ExperienceLevel] | None, Query()] = None,
    source: str | None = None,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    registry: ProviderRegistry = Depends(get_job_sources),
) -> DiscoveryResultsOut:
    """Search every enabled source. Filters apply the same way to every source."""
    query = JobSearchQuery(
        role=role or None,
        location=location or None,
        remote=remote,
        employment_types=employment_type or [],
        skills=[s.strip() for s in skills or [] if s.strip()],
        experience_levels=experience_level or [],
        source=source,
        page=page,
        page_size=page_size,
    )
    return await service.search(session, user, query, registry)


@router.get("/jobs/{source}/{source_identifier}", response_model=DiscoveredJobOut)
async def get_posting(
    source: str,
    source_identifier: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    registry: ProviderRegistry = Depends(get_job_sources),
) -> DiscoveredJobOut:
    return await service.get(session, user, registry, source, source_identifier)


@router.post("/jobs/{source}/{source_identifier}/import", response_model=ImportResultOut)
async def import_posting(
    source: str,
    source_identifier: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_job_sources),
    llm: LLMProvider | None = Depends(get_job_llm),
) -> ImportResultOut:
    """Import the posting as one of your jobs, analyzed like a pasted description, so it
    can be matched and tailored for. Importing again returns the existing job (200)."""
    result = await service.import_job(
        session, user, registry, source, source_identifier, settings, llm
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return result
