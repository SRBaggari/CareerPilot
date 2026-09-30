"""The claim verification engine as a standalone service: check any claims or text against
your verified evidence and stored profile. Nothing is stored."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider, get_llm_provider
from app.api.routes.candidate_evidence import get_embedder
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.users.dependencies import get_current_user
from app.users.models import User
from app.verification import service
from app.verification.service import ClaimCheckIn
from app.verification.types import VerificationReportOut

router = APIRouter(prefix="/api/v1/verification", tags=["verification"])


def get_verification_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    return get_llm_provider(settings)


@router.post("/check", response_model=VerificationReportOut)
async def check_claims(
    payload: ClaimCheckIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_verification_llm),
) -> VerificationReportOut:
    """Verify claims (or free text, split into sentences) against your evidence. Each claim
    gets SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONTRADICTED with a reason."""
    return await service.check_claims(session, user, payload, embedder, llm, settings)
