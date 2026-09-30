"""Job-specific resume tailoring: generate, preview, edit and download.

Tailored resumes are stored separately from the uploaded master resumes, and every claim
in them is verified against the candidate's evidence before it is kept.
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
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.documents.resume import render, service
from app.documents.resume.schemas import TailoredResumeEdit, TailoredResumeOut
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1", tags=["tailored resumes"])

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def get_tailor_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    return get_llm_provider(settings)


@router.post(
    "/jobs/{job_id}/tailored-resumes",
    response_model=TailoredResumeOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_tailored_resume(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_tailor_llm),
    match_llm: LLMProvider | None = Depends(get_match_llm),
) -> TailoredResumeOut:
    """Generate (or regenerate) a resume tailored to this job from your verified evidence.
    Replaces earlier unapproved versions for the job."""
    return await service.generate(session, user, job_id, embedder, llm, settings, match_llm)


@router.get("/jobs/{job_id}/tailored-resumes/latest", response_model=TailoredResumeOut)
async def get_latest_tailored_resume(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TailoredResumeOut:
    return await service.latest(session, user, job_id)


@router.get("/tailored-resumes/{resume_id}", response_model=TailoredResumeOut)
async def get_tailored_resume(
    resume_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TailoredResumeOut:
    return await service.output(session, await service.get(session, user, resume_id))


@router.put("/tailored-resumes/{resume_id}", response_model=TailoredResumeOut)
async def edit_tailored_resume(
    resume_id: uuid.UUID,
    payload: TailoredResumeEdit,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TailoredResumeOut:
    """Save edits. Unsupported statements are rejected (422) with a reason for each."""
    return await service.update(session, user, resume_id, payload.content)


@router.get("/tailored-resumes/{resume_id}/download")
async def download_tailored_resume(
    resume_id: uuid.UUID,
    format: Literal["pdf", "docx"] = Query("pdf"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Response:
    resume = await service.get(session, user, resume_id)
    content = service.as_content(resume)
    body = render.to_pdf(content) if format == "pdf" else render.to_docx(content)
    stem = re.sub(r"[^A-Za-z0-9]+", "-", content.header.full_name).strip("-") or "resume"
    filename = f"{stem}-resume-v{resume.version}.{format}"
    return Response(
        body,
        media_type=MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete(
    "/tailored-resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_tailored_resume(
    resume_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    await service.delete_resume(session, user, resume_id)
