"""AI-generated profile suggestions: stored apart from the profile, applied only on accept.

Future AI features (resume extraction, enrichment) call ``create_suggestion``. Nothing in
this module writes profile tables except ``accept_suggestion``, which is triggered by the
candidate and applies the change through the regular, validated service functions.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, FieldValidationError, NotFoundError
from app.profiles import service
from app.profiles.models import (
    CandidateProfile,
    EvidenceOrigin,
    ProfileSuggestion,
    SuggestionAction,
    SuggestionSection,
    SuggestionSource,
    SuggestionStatus,
)
from app.profiles.schemas import (
    EvidenceIn,
    EvidenceUpdate,
    ProfilePatch,
    SkillIn,
    SuggestionOut,
)
from app.users.models import User

# Suggestion section -> profile section spec (for the list-style sections).
_SECTION_SPECS: dict[SuggestionSection, service.SectionSpec[Any]] = {
    SuggestionSection.EDUCATION: service.SECTIONS["educations"],
    SuggestionSection.WORK_EXPERIENCE: service.SECTIONS["work-experiences"],
    SuggestionSection.PROJECT: service.SECTIONS["projects"],
    SuggestionSection.CERTIFICATION: service.SECTIONS["certifications"],
    SuggestionSection.ACHIEVEMENT: service.SECTIONS["achievements"],
    SuggestionSection.COURSEWORK: service.SECTIONS["coursework"],
}


def _validate_proposal(
    section: SuggestionSection, action: SuggestionAction, data: dict[str, Any]
) -> None:
    """Reject malformed AI output before it is ever stored."""
    if section == SuggestionSection.PERSONAL_INFO:
        if action != SuggestionAction.UPDATE:
            raise FieldValidationError("action", "personal_info suggestions must be updates")
        ProfilePatch.model_validate(data)
    elif section in _SECTION_SPECS:
        spec = _SECTION_SPECS[section]
        if action == SuggestionAction.CREATE:
            spec.schema_in.model_validate(data)
        elif unknown := set(data) - set(spec.schema_in.model_fields):
            raise FieldValidationError("proposed_data", f"unknown fields: {sorted(unknown)}")
    elif section == SuggestionSection.SKILL:
        if action != SuggestionAction.CREATE:
            raise FieldValidationError("action", "skill suggestions must be creates")
        SkillIn.model_validate(data)
    elif section == SuggestionSection.EVIDENCE:
        schema: type[BaseModel] = (
            EvidenceIn if action == SuggestionAction.CREATE else EvidenceUpdate
        )
        schema.model_validate(data)


async def create_suggestion(
    session: AsyncSession,
    profile: CandidateProfile,
    *,
    section: SuggestionSection,
    action: SuggestionAction,
    proposed_data: dict[str, Any],
    source: SuggestionSource,
    rationale: str | None = None,
    target_id: uuid.UUID | None = None,
    ai_execution_log_id: uuid.UUID | None = None,
) -> ProfileSuggestion:
    _validate_proposal(section, action, proposed_data)
    suggestion = ProfileSuggestion(
        candidate_profile_id=profile.id,
        section=section,
        action=action,
        target_id=target_id,
        proposed_data=proposed_data,
        source=source,
        rationale=rationale,
        ai_execution_log_id=ai_execution_log_id,
    )
    session.add(suggestion)
    await session.commit()
    return suggestion


async def list_suggestions(
    session: AsyncSession, user: User, status: SuggestionStatus | None
) -> list[SuggestionOut]:
    profile = await service.get_profile(session, user)
    query = select(ProfileSuggestion).where(ProfileSuggestion.candidate_profile_id == profile.id)
    if status is not None:
        query = query.where(ProfileSuggestion.status == status)
    rows = await session.scalars(query.order_by(ProfileSuggestion.created_at))
    return [SuggestionOut.model_validate(row) for row in rows]


async def _get_pending(
    session: AsyncSession, user: User, suggestion_id: uuid.UUID
) -> ProfileSuggestion:
    profile = await service.get_profile(session, user)
    suggestion = await session.scalar(
        select(ProfileSuggestion).where(
            ProfileSuggestion.id == suggestion_id,
            ProfileSuggestion.candidate_profile_id == profile.id,
        )
    )
    if suggestion is None:
        raise NotFoundError("Suggestion not found.")
    if suggestion.status != SuggestionStatus.PENDING:
        raise ConflictError(f"Suggestion was already {suggestion.status.value}.")
    return suggestion


def _require_target(suggestion: ProfileSuggestion) -> uuid.UUID:
    if suggestion.target_id is None:
        raise ConflictError("Suggestion has no target to update.")
    return suggestion.target_id


async def _apply(session: AsyncSession, user: User, s: ProfileSuggestion) -> uuid.UUID:
    data = s.proposed_data
    if s.section == SuggestionSection.PERSONAL_INFO:
        profile_out = await service.update_profile(session, user, ProfilePatch.model_validate(data))
        return profile_out.id

    if s.section in _SECTION_SPECS:
        spec = _SECTION_SPECS[s.section]
        if s.action == SuggestionAction.CREATE:
            created = await service.create_item(
                session, user, spec, spec.schema_in.model_validate(data)
            )
            return created.id
        profile = await service.get_profile(session, user)
        item = await service.get_item(session, spec, profile, _require_target(s))
        merged = service.item_fields(spec, item) | data  # validate the result as a whole
        replaced = await service.replace_item(
            session, user, spec, item.id, spec.schema_in.model_validate(merged)
        )
        return replaced.id

    if s.section == SuggestionSection.SKILL:
        return (await service.add_skill(session, user, SkillIn.model_validate(data))).id

    if s.action == SuggestionAction.CREATE:  # evidence
        origin = (
            EvidenceOrigin.RESUME_EXTRACTED
            if s.source == SuggestionSource.RESUME_EXTRACTION
            else EvidenceOrigin.AI_SUGGESTED
        )
        evidence = await service.add_evidence(
            session, user, EvidenceIn.model_validate(data), origin=origin
        )
        return evidence.id
    update = EvidenceUpdate.model_validate(data)
    return (await service.update_evidence(session, user, _require_target(s), update.content)).id


async def accept_suggestion(
    session: AsyncSession, user: User, suggestion_id: uuid.UUID
) -> SuggestionOut:
    """Apply a suggestion as an explicit user action. The status change is committed in the
    same transaction as the profile change, so they succeed or fail together."""
    suggestion = await _get_pending(session, user, suggestion_id)
    suggestion.status = SuggestionStatus.ACCEPTED
    suggestion.reviewed_at = datetime.now(UTC)
    try:
        suggestion.applied_target_id = await _apply(session, user, suggestion)
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    await session.refresh(suggestion)
    return SuggestionOut.model_validate(suggestion)


async def reject_suggestion(
    session: AsyncSession, user: User, suggestion_id: uuid.UUID
) -> SuggestionOut:
    suggestion = await _get_pending(session, user, suggestion_id)
    suggestion.status = SuggestionStatus.REJECTED
    suggestion.reviewed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(suggestion)
    return SuggestionOut.model_validate(suggestion)
