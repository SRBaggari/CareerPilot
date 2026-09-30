"""Master resume upload and parsing. Parsed information is returned as pending
suggestions for review; it never modifies the profile directly."""

import uuid

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMProvider, get_llm_provider
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.resumes import service
from app.resumes.schemas import ResumeDetail, ResumeOut
from app.resumes.storage import FileStorage, get_storage
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1/resumes", tags=["resumes"])


def get_resume_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    return get_llm_provider(settings)


@router.post("", response_model=ResumeDetail, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile = File(..., description="PDF or DOCX resume"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    storage: FileStorage = Depends(get_storage),
    llm: LLMProvider | None = Depends(get_resume_llm),
) -> ResumeDetail:
    # Read at most one byte past the limit so oversized uploads are rejected cheaply.
    data = await file.read(settings.max_resume_bytes + 1)
    return await service.ingest_resume(
        session, user, filename=file.filename, data=data, settings=settings,
        storage=storage, llm=llm,
    )  # fmt: skip


@router.get("", response_model=list[ResumeOut])
async def list_resumes(
    session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> list[ResumeOut]:
    return await service.list_resumes(session, user)


@router.get("/{resume_id}", response_model=ResumeDetail)
async def get_resume(
    resume_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ResumeDetail:
    return await service.get_resume(session, user, resume_id)


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_resume(
    resume_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    storage: FileStorage = Depends(get_storage),
) -> None:
    await service.delete_resume(session, user, resume_id, storage)
