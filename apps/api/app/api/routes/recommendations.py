"""Personalized job recommendations: refresh, list, and act (save, ignore, analyze,
tailor, start an application). Each recommendation explains why it is recommended."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.discovery import get_job_sources
from app.api.routes.jobs import get_job_llm
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.discovery.registry import ProviderRegistry
from app.recommendations import service
from app.recommendations.models import RecommendationStatus
from app.recommendations.schemas import ActionResultOut, RecommendationListOut
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationListOut)
async def list_recommendations(
    view: Literal["recommended", "saved", "ignored", "filtered_out"] = "recommended",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    registry: ProviderRegistry = Depends(get_job_sources),
) -> RecommendationListOut:
    """Stored recommendations. ``filtered_out`` lists jobs eligibility filtering removed,
    each with the reason."""
    return await service.list_recommendations(session, user, registry, view)


@router.post("/refresh", response_model=RecommendationListOut)
async def refresh_recommendations(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    registry: ProviderRegistry = Depends(get_job_sources),
    embedder: EmbeddingProvider = Depends(get_embedder),
) -> RecommendationListOut:
    """Discover jobs, match them against your verified evidence, filter by eligibility and
    preferences, and explain each recommendation."""
    return await service.refresh(session, user, registry, embedder)


async def _status(
    rec_id: uuid.UUID, status: RecommendationStatus, session: AsyncSession, user: User
) -> ActionResultOut:
    return await service.set_status(session, user, rec_id, status)


@router.post("/{rec_id}/save", response_model=ActionResultOut)
async def save_recommendation(
    rec_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ActionResultOut:
    return await _status(rec_id, RecommendationStatus.SAVED, session, user)


@router.post("/{rec_id}/ignore", response_model=ActionResultOut)
async def ignore_recommendation(
    rec_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ActionResultOut:
    """Hide the job; it stays hidden across refreshes until restored."""
    return await _status(rec_id, RecommendationStatus.IGNORED, session, user)


@router.post("/{rec_id}/restore", response_model=ActionResultOut)
async def restore_recommendation(
    rec_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ActionResultOut:
    """Undo save or ignore."""
    return await _status(rec_id, RecommendationStatus.NEW, session, user)


@router.post("/{rec_id}/analyze", response_model=ActionResultOut)
async def analyze_recommendation(
    rec_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_job_sources),
    llm: LLMProvider | None = Depends(get_job_llm),
) -> ActionResultOut:
    """Import the job with full analysis (used by Analyze Job and Tailor Resume)."""
    return await service.analyze(session, user, rec_id, registry, settings, llm)


@router.post("/{rec_id}/start-application", response_model=ActionResultOut)
async def start_application(
    rec_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    registry: ProviderRegistry = Depends(get_job_sources),
    llm: LLMProvider | None = Depends(get_job_llm),
) -> ActionResultOut:
    """Start a draft application. Nothing is submitted without your explicit approval."""
    return await service.start_application(session, user, rec_id, registry, settings, llm)
