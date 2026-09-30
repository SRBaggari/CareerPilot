"""Candidate profile API: the master profile, its sections, evidence, skills, and the
review queue for AI suggestions."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.profiles import service, suggestions
from app.profiles.models import SuggestionStatus
from app.profiles.schemas import (
    AcceptSuggestionIn,
    EvidenceIn,
    EvidenceOut,
    EvidenceUpdate,
    ProfileIn,
    ProfileOut,
    ProfilePatch,
    SkillIn,
    SkillOut,
    SkillUpdate,
    SuggestionOut,
)
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])

Session = Depends(get_session)
CurrentUser = Depends(get_current_user)


# --- Profile ----------------------------------------------------------------------------


@router.get("", response_model=ProfileOut)
async def get_profile(session: AsyncSession = Session, user: User = CurrentUser) -> ProfileOut:
    return await service.read_profile(session, user)


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: ProfileIn, session: AsyncSession = Session, user: User = CurrentUser
) -> ProfileOut:
    return await service.create_profile(session, user, payload)


@router.patch("", response_model=ProfileOut)
async def update_profile(
    payload: ProfilePatch, session: AsyncSession = Session, user: User = CurrentUser
) -> ProfileOut:
    return await service.update_profile(session, user, payload)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_profile(session: AsyncSession = Session, user: User = CurrentUser) -> None:
    await service.delete_profile(session, user)


# --- Sections (educations, work-experiences, projects, ...) -----------------------------


def _register_section(spec: service.SectionSpec[Any]) -> None:
    """Add POST/PUT/DELETE routes for one section.

    The request-body annotation is set per section after definition so FastAPI validates
    and documents the section's own schema.
    """

    async def create(
        payload: BaseModel, session: AsyncSession = Session, user: User = CurrentUser
    ) -> Any:
        return await service.create_item(session, user, spec, payload)

    async def replace(
        item_id: uuid.UUID,
        payload: BaseModel,
        session: AsyncSession = Session,
        user: User = CurrentUser,
    ) -> Any:
        return await service.replace_item(session, user, spec, item_id, payload)

    async def delete(
        item_id: uuid.UUID, session: AsyncSession = Session, user: User = CurrentUser
    ) -> None:
        await service.delete_item(session, user, spec, item_id)

    create.__annotations__["payload"] = spec.schema_in
    replace.__annotations__["payload"] = spec.schema_in
    name = spec.path.replace("-", "_")
    router.add_api_route(
        f"/{spec.path}",
        create,
        methods=["POST"],
        response_model=spec.schema_out,
        status_code=status.HTTP_201_CREATED,
        name=f"create_{name}",
    )
    router.add_api_route(
        f"/{spec.path}/{{item_id}}",
        replace,
        methods=["PUT"],
        response_model=spec.schema_out,
        name=f"replace_{name}",
    )
    router.add_api_route(
        f"/{spec.path}/{{item_id}}",
        delete,
        methods=["DELETE"],
        name=f"delete_{name}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
    )


for _spec in service.SECTIONS.values():
    _register_section(_spec)


# --- Evidence ---------------------------------------------------------------------------


@router.post("/evidence", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED)
async def add_evidence(
    payload: EvidenceIn, session: AsyncSession = Session, user: User = CurrentUser
) -> EvidenceOut:
    return await service.add_evidence(session, user, payload)


@router.patch("/evidence/{evidence_id}", response_model=EvidenceOut)
async def update_evidence(
    evidence_id: uuid.UUID,
    payload: EvidenceUpdate,
    session: AsyncSession = Session,
    user: User = CurrentUser,
) -> EvidenceOut:
    return await service.update_evidence(session, user, evidence_id, payload.content)


@router.delete(
    "/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_evidence(
    evidence_id: uuid.UUID, session: AsyncSession = Session, user: User = CurrentUser
) -> None:
    await service.delete_evidence(session, user, evidence_id)


# --- Skills -----------------------------------------------------------------------------


@router.post("/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def add_skill(
    payload: SkillIn, session: AsyncSession = Session, user: User = CurrentUser
) -> SkillOut:
    return await service.add_skill(session, user, payload)


@router.put("/skills/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: uuid.UUID,
    payload: SkillUpdate,
    session: AsyncSession = Session,
    user: User = CurrentUser,
) -> SkillOut:
    return await service.update_skill(session, user, skill_id, payload)


@router.delete(
    "/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_skill(
    skill_id: uuid.UUID, session: AsyncSession = Session, user: User = CurrentUser
) -> None:
    await service.delete_skill(session, user, skill_id)


# --- AI suggestions (review queue) ------------------------------------------------------


@router.get("/suggestions", response_model=list[SuggestionOut])
async def list_suggestions(
    status_filter: SuggestionStatus | None = SuggestionStatus.PENDING,
    resume_id: uuid.UUID | None = None,
    session: AsyncSession = Session,
    user: User = CurrentUser,
) -> list[SuggestionOut]:
    return await suggestions.list_suggestions(session, user, status_filter, resume_id)


@router.post("/suggestions/{suggestion_id}/accept", response_model=SuggestionOut)
async def accept_suggestion(
    suggestion_id: uuid.UUID,
    payload: AcceptSuggestionIn | None = None,
    session: AsyncSession = Session,
    user: User = CurrentUser,
) -> SuggestionOut:
    """Accept as proposed, or send ``{"proposed_data": {...}}`` to accept an edited version."""
    edited = payload.proposed_data if payload is not None else None
    return await suggestions.accept_suggestion(session, user, suggestion_id, edited)


@router.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionOut)
async def reject_suggestion(
    suggestion_id: uuid.UUID, session: AsyncSession = Session, user: User = CurrentUser
) -> SuggestionOut:
    return await suggestions.reject_suggestion(session, user, suggestion_id)
