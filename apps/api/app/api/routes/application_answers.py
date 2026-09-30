"""Answers to application questions: generate, list, edit, regenerate, approve, delete.

Each answer is written from the candidate's verified evidence and every sentence is
checked by the claim verification engine. Approval re-verifies against the current profile.
"""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider, get_llm_provider
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.matching import get_match_llm
from app.api.routes.verification import get_verification_llm
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.documents.answers import service
from app.documents.answers.schemas import AnswerEdit, ApplicationAnswerOut, QuestionsIn
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1", tags=["application answers"])


def get_answer_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    return get_llm_provider(settings)


@router.post(
    "/jobs/{job_id}/application-answers",
    response_model=list[ApplicationAnswerOut],
    status_code=status.HTTP_201_CREATED,
)
async def answer_questions(
    job_id: uuid.UUID,
    payload: QuestionsIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_answer_llm),
    match_llm: LLMProvider | None = Depends(get_match_llm),
    verify_llm: LLMProvider | None = Depends(get_verification_llm),
) -> list[ApplicationAnswerOut]:
    """Answer application questions for this job from your verified evidence."""
    return await service.create(
        session, user, job_id, payload, embedder, llm, settings, match_llm, verify_llm
    )


@router.get("/jobs/{job_id}/application-answers", response_model=list[ApplicationAnswerOut])
async def list_answers(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[ApplicationAnswerOut]:
    return await service.list_for_job(session, user, job_id)


@router.get("/application-answers/{answer_id}", response_model=ApplicationAnswerOut)
async def get_answer(
    answer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ApplicationAnswerOut:
    return await service.get(session, user, answer_id)


@router.put("/application-answers/{answer_id}", response_model=ApplicationAnswerOut)
async def edit_answer(
    answer_id: uuid.UUID,
    payload: AnswerEdit,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_verification_llm),
) -> ApplicationAnswerOut:
    """Save your edited answer. Every sentence is verified first; anything unsupported is
    rejected (422) with a reason, and nothing is saved. Editing withdraws an approval."""
    return await service.update(session, user, answer_id, payload, embedder, llm, settings)


@router.post("/application-answers/{answer_id}/regenerate", response_model=ApplicationAnswerOut)
async def regenerate_answer(
    answer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_answer_llm),
    match_llm: LLMProvider | None = Depends(get_match_llm),
    verify_llm: LLMProvider | None = Depends(get_verification_llm),
) -> ApplicationAnswerOut:
    return await service.regenerate(
        session, user, answer_id, embedder, llm, settings, match_llm, verify_llm
    )


@router.post("/application-answers/{answer_id}/approve", response_model=ApplicationAnswerOut)
async def approve_answer(
    answer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_verification_llm),
) -> ApplicationAnswerOut:
    """Approve the answer. It is verified once more first; if it no longer passes, it is
    marked as failing verification and approval is refused (409)."""
    return await service.approve(session, user, answer_id, embedder, llm, settings)


@router.delete(
    "/application-answers/{answer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_answer(
    answer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    await service.delete_answer(session, user, answer_id)
