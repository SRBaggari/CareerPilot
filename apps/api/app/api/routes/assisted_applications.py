"""Browser-assisted applications.

CareerPilot fills a supported application form from your approved materials, then pauses
and shows you exactly what would be submitted. It submits only after you confirm that
exact review. Unsupported sites, sign-in walls, CAPTCHAs and refusals stop the run with an
explanation; they are never bypassed. Every action is in the run's audit log.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.verification import get_verification_llm
from app.automation import service
from app.automation.schemas import RunConfirm, RunInputs, RunOut, RunStart
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1", tags=["assisted applications"])
Session = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.post("/applications/{application_id}/assisted-runs", response_model=RunOut)
async def start_run(
    application_id: uuid.UUID,
    payload: RunStart,
    response: Response,
    session: Session,
    user: CurrentUser,
    settings: AppSettings,
) -> RunOut:
    """Fill the approved application for your review. Never submits."""
    response.status_code = status.HTTP_201_CREATED
    return await service.start(session, user, application_id, payload.inputs, settings)


@router.get("/applications/{application_id}/assisted-runs", response_model=list[RunOut])
async def list_runs(application_id: uuid.UUID, session: Session, user: CurrentUser) -> list[RunOut]:
    return await service.list_runs(session, user, application_id)


@router.get("/assisted-runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: Session, user: CurrentUser) -> RunOut:
    return await service.get(session, user, run_id)


@router.post("/assisted-runs/{run_id}/inputs", response_model=RunOut)
async def provide_inputs(
    run_id: uuid.UUID,
    payload: RunInputs,
    session: Session,
    user: CurrentUser,
    settings: AppSettings,
) -> RunOut:
    """Provide values for fields CareerPilot can't fill from your records, then refill."""
    return await service.provide_inputs(session, user, run_id, payload.inputs, settings)


@router.post("/assisted-runs/{run_id}/submit", response_model=RunOut)
async def submit_run(
    run_id: uuid.UUID,
    payload: RunConfirm,
    session: Session,
    user: CurrentUser,
    settings: AppSettings,
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_verification_llm),
) -> RunOut:
    """Submit, only with your explicit confirmation of the exact review you saw. The resume
    and cover letter are verified again against your current evidence first."""
    return await service.submit(
        session, user, run_id, payload.review_hash, payload.confirm, settings, embedder, llm
    )


@router.post("/assisted-runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(run_id: uuid.UUID, session: Session, user: CurrentUser) -> RunOut:
    return await service.cancel(session, user, run_id)
