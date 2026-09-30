"""Candidate evidence retrieval (RAG) API."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingError, EmbeddingProvider, get_embedding_provider
from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailableError
from app.db.session import get_session
from app.retrieval import service
from app.retrieval.schemas import (
    EvidenceIndexIn,
    EvidenceIndexOut,
    EvidenceSearchIn,
    EvidenceSearchOut,
)
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/candidate/evidence", tags=["candidate evidence"])


def get_embedder(settings: Settings = Depends(get_settings)) -> EmbeddingProvider:
    try:
        return get_embedding_provider(settings)
    except EmbeddingError as exc:
        raise ServiceUnavailableError(f"Evidence search is unavailable: {exc}") from exc


@router.post("/search", response_model=EvidenceSearchOut)
async def search_evidence(
    payload: EvidenceSearchIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    embedder: EmbeddingProvider = Depends(get_embedder),
) -> EvidenceSearchOut:
    """Top-k semantic search over one candidate's evidence (verified only by default)."""
    profile = await service.resolve_candidate(session, user, payload.candidate_id)
    return await service.search_evidence(
        session,
        profile.id,
        payload.query,
        embedder,
        top_k=payload.top_k,
        evidence_types=[payload.evidence_type] if payload.evidence_type else None,
        include_unverified=payload.include_unverified,
        min_similarity=payload.min_similarity,
    )


@router.post("/index", response_model=EvidenceIndexOut)
async def index_evidence(
    payload: EvidenceIndexIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    embedder: EmbeddingProvider = Depends(get_embedder),
) -> EvidenceIndexOut:
    """(Re)build the candidate's embeddings. Search also indexes on demand."""
    profile = await service.resolve_candidate(session, user, payload.candidate_id)
    return await service.index_evidence(session, profile.id, embedder, force=payload.force)
