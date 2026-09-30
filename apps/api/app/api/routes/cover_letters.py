"""Job-specific cover letters: generate, preview, edit (save), verify, and download.

Every sentence is checked by the claim verification engine; sentences it doesn't approve
are regenerated from the candidate's evidence or removed.
"""

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider, get_llm_provider
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.matching import get_match_llm
from app.api.routes.tailored_resumes import MEDIA_TYPES
from app.api.routes.verification import get_verification_llm
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.documents.cover_letter import render, service
from app.documents.cover_letter.schemas import CoverLetterEdit, CoverLetterOut
from app.users.dependencies import get_current_user
from app.users.models import User
from app.verification import service as verification
from app.verification.types import VerificationReportOut

router = APIRouter(prefix="/api/v1", tags=["cover letters"])


def get_letter_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    return get_llm_provider(settings)


@router.post(
    "/jobs/{job_id}/cover-letters",
    response_model=CoverLetterOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_cover_letter(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_letter_llm),
    match_llm: LLMProvider | None = Depends(get_match_llm),
    verify_llm: LLMProvider | None = Depends(get_verification_llm),
) -> CoverLetterOut:
    """Generate (or regenerate) a cover letter for this job from your verified evidence.
    Replaces earlier unapproved versions for the job."""
    return await service.generate(
        session, user, job_id, embedder, llm, settings, match_llm, verify_llm
    )


@router.get("/jobs/{job_id}/cover-letters/latest", response_model=CoverLetterOut)
async def get_latest_cover_letter(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CoverLetterOut:
    return await service.latest(session, user, job_id)


@router.get("/cover-letters/{letter_id}", response_model=CoverLetterOut)
async def get_cover_letter(
    letter_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CoverLetterOut:
    return await service.output(session, await service.get(session, user, letter_id))


@router.put("/cover-letters/{letter_id}", response_model=CoverLetterOut)
async def save_cover_letter(
    letter_id: uuid.UUID,
    payload: CoverLetterEdit,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_verification_llm),
) -> CoverLetterOut:
    """Save your edits. Every sentence is verified first; anything the engine doesn't
    approve is rejected (422) with a reason, and nothing is saved."""
    return await service.update(session, user, letter_id, payload, embedder, llm, settings)


@router.post("/cover-letters/{letter_id}/verify", response_model=CoverLetterOut)
async def reverify_cover_letter(
    letter_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_verification_llm),
) -> CoverLetterOut:
    """Verify the letter again against your current evidence and profile."""
    return await service.reverify(session, user, letter_id, embedder, llm, settings)


@router.get(
    "/cover-letters/{letter_id}/verification-reports",
    response_model=list[VerificationReportOut],
)
async def list_cover_letter_reports(
    letter_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[VerificationReportOut]:
    letter = await service.get(session, user, letter_id)
    return await verification.reports_for(session, cover_letter_id=letter.id)


@router.get("/cover-letters/{letter_id}/download")
async def download_cover_letter(
    letter_id: uuid.UUID,
    format: Literal["pdf", "docx"] = Query("pdf"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Response:
    letter = await service.get(session, user, letter_id)
    content = service.as_content(letter)
    on = letter.updated_at.date()
    body = render.to_pdf(content, on) if format == "pdf" else render.to_docx(content, on)
    stem = re.sub(r"[^A-Za-z0-9]+", "-", content.signature.full_name).strip("-") or "cover"
    filename = f"{stem}-cover-letter-v{letter.version}.{format}"
    return Response(
        body,
        media_type=MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete(
    "/cover-letters/{letter_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_cover_letter(
    letter_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    await service.delete_letter(session, user, letter_id)
