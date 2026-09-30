"""Job description analysis API. Job URLs are stored for reference and never fetched."""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMProvider, get_llm_provider
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.jobs import service
from app.jobs.schemas import JobAnalyzeIn, JobManualIn, JobOut, JobSummaryOut
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def get_job_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    return get_llm_provider(settings)


@router.post("/analyze", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def analyze_job(
    payload: JobAnalyzeIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    llm: LLMProvider | None = Depends(get_job_llm),
) -> JobOut:
    """Analyze a pasted job description and store the structured result."""
    return await service.analyze_job(session, user, payload, settings, llm)


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobManualIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JobOut:
    """Store a manually entered job exactly as given."""
    return await service.create_manual_job(session, user, payload)


@router.get("", response_model=list[JobSummaryOut])
async def list_jobs(
    session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> list[JobSummaryOut]:
    return await service.list_jobs(session, user)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JobOut:
    return await service.get_job(session, user, job_id)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    await service.delete_job(session, user, job_id)
