"""Candidate-job matching API: an explainable evidence-coverage report per job."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider, get_llm_provider
from app.api.routes.candidate_evidence import get_embedder
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.matching import service
from app.matching.schemas import MatchReportOut
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1/jobs", tags=["matching"])


def get_match_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    return get_llm_provider(settings)


@router.post("/{job_id}/match", response_model=MatchReportOut)
async def compute_match(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_match_llm),
) -> MatchReportOut:
    """(Re)compute the match between your verified profile evidence and this job."""
    return await service.compute_match(session, user, job_id, embedder, llm, settings)


@router.get("/{job_id}/match", response_model=MatchReportOut)
async def get_match(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> MatchReportOut:
    """The latest match report; ``is_stale`` says whether your profile changed since."""
    return await service.get_report(session, user, job_id)
