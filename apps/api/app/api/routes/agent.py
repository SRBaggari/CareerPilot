"""The CareerPilot agent: orchestrates the profile, discovery, analysis, matching, resume,
cover letter, verification, preparation, approval and tracking agents through a validated
state machine. It stops for a human whenever one is needed, never approves or submits,
and logs every action (without secrets)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import service
from app.agent.service import AdvanceIn, RunOut, RunStart, ToolOut
from app.agent.tools import Deps
from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider, get_llm_provider
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.discovery import get_job_sources
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.discovery.registry import ProviderRegistry
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
Session = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def get_agent_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    return get_llm_provider(settings)


def get_deps(
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_agent_llm),
    registry: ProviderRegistry = Depends(get_job_sources),
) -> Deps:
    return Deps(settings=settings, embedder=embedder, llm=llm, registry=registry)


AgentDeps = Annotated[Deps, Depends(get_deps)]


@router.get("/tools", response_model=list[ToolOut])
async def list_tools() -> list[ToolOut]:
    """The explicit tools each agent may use, by stage."""
    return service.tools()


@router.post("/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def start_run(
    payload: RunStart, session: Session, user: CurrentUser, deps: AgentDeps
) -> RunOut:
    """Create a run for a job (or a posting to import). Nothing runs until you advance it."""
    return await service.start(session, user, payload, deps)


@router.get("/runs", response_model=list[RunOut])
async def list_runs(session: Session, user: CurrentUser) -> list[RunOut]:
    return await service.list_runs(session, user)


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: Session, user: CurrentUser) -> RunOut:
    return await service.get(session, user, run_id)


@router.post("/runs/{run_id}/advance", response_model=RunOut)
async def advance_run(
    run_id: uuid.UUID,
    payload: AdvanceIn,
    session: Session,
    user: CurrentUser,
    deps: AgentDeps,
) -> RunOut:
    """Run stages until the agent needs you, completes, or reaches ``max_stages``."""
    return await service.advance(session, user, run_id, payload, deps)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    run_id: uuid.UUID, session: Session, user: CurrentUser, deps: AgentDeps
) -> RunOut:
    return await service.cancel(session, user, run_id, deps)
