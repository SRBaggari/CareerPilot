"""AI-generated profile suggestions: stored apart from the profile, applied only on accept.

AI features (resume extraction, enrichment) call ``create_suggestion``. Nothing in this module
writes profile tables except ``accept_suggestion``, which is triggered by the candidate and
applies the change through the regular, validated service functions, in one transaction.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, FieldValidationError, NotFoundError
from app.profiles import service
from app.profiles.models import (
    CandidateEvidence,
    CandidateProfile,
    EvidenceOrigin,
    EvidenceSourceType,
    ProfileSuggestion,
    SuggestionAction,
    SuggestionSection,
    SuggestionSource,
    SuggestionStatus,
    candidate_evidence_skills,
)
from app.profiles.schemas import (
    EvidenceIn,
    EvidenceUpdate,
    ProfileIn,
    ProfilePatch,
    SkillIn,
    SuggestionOut,
)
from app.users.models import User

# Suggestion section -> profile section spec (for the list-style sections).
SECTION_SPECS: dict[SuggestionSection, service.SectionSpec[Any]] = {
    SuggestionSection.EDUCATION: service.SECTIONS["educations"],
    SuggestionSection.WORK_EXPERIENCE: service.SECTIONS["work-experiences"],
    SuggestionSection.PROJECT: service.SECTIONS["projects"],
    SuggestionSection.CERTIFICATION: service.SECTIONS["certifications"],
    SuggestionSection.ACHIEVEMENT: service.SECTIONS["achievements"],
    SuggestionSection.COURSEWORK: service.SECTIONS["coursework"],
}
HIGHLIGHTS = "highlights"  # extra key on item "create" proposals: one claim per entry
MAX_HIGHLIGHTS = 30


def _validate_highlights(value: Any) -> None:
    if not isinstance(value, list) or len(value) > MAX_HIGHLIGHTS:
        raise FieldValidationError(HIGHLIGHTS, f"must be a list of at most {MAX_HIGHLIGHTS}")
    for text in value:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise FieldValidationError(HIGHLIGHTS, "each highlight must be 1-2000 characters")


def validate_proposal(
    section: SuggestionSection, action: SuggestionAction, data: dict[str, Any]
) -> None:
    """Reject malformed AI output (or malformed user edits) before it is stored/applied."""
    if section == SuggestionSection.PERSONAL_INFO:
        if action != SuggestionAction.UPDATE:
            raise FieldValidationError("action", "personal_info suggestions must be updates")
        ProfilePatch.model_validate(data)
        # Full field validation; full_name is a stand-in only when it isn't proposed.
        ProfileIn.model_validate({"full_name": "-", **data})
    elif section in SECTION_SPECS:
        spec = SECTION_SPECS[section]
        if action == SuggestionAction.CREATE:
            fields = dict(data)
            if HIGHLIGHTS in fields:
                _validate_highlights(fields.pop(HIGHLIGHTS))
            spec.schema_in.model_validate(fields)
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
    resume_id: uuid.UUID | None = None,
    source_excerpt: str | None = None,
    ai_execution_log_id: uuid.UUID | None = None,
    commit: bool = True,
) -> ProfileSuggestion:
    validate_proposal(section, action, proposed_data)
    suggestion = ProfileSuggestion(
        candidate_profile_id=profile.id,
        section=section,
        action=action,
        target_id=target_id,
        proposed_data=proposed_data,
        source=source,
        rationale=rationale,
        resume_id=resume_id,
        source_excerpt=source_excerpt or None,
        ai_execution_log_id=ai_execution_log_id,
    )
    session.add(suggestion)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return suggestion


async def list_suggestions(
    session: AsyncSession,
    user: User,
    status: SuggestionStatus | None,
    resume_id: uuid.UUID | None = None,
) -> list[SuggestionOut]:
    profile = await service.get_profile(session, user)
    query = select(ProfileSuggestion).where(ProfileSuggestion.candidate_profile_id == profile.id)
    if status is not None:
        query = query.where(ProfileSuggestion.status == status)
    if resume_id is not None:
        query = query.where(ProfileSuggestion.resume_id == resume_id)
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


def _origin(s: ProfileSuggestion) -> EvidenceOrigin:
    if s.source == SuggestionSource.RESUME_EXTRACTION:
        return EvidenceOrigin.RESUME_EXTRACTED
    return EvidenceOrigin.AI_SUGGESTED


async def _profile_evidence(
    session: AsyncSession, user: User, s: ProfileSuggestion, content: str
) -> uuid.UUID:
    """Profile-level evidence with this content from the same resume, created if needed."""
    profile = await service.get_profile(session, user)
    existing = await session.scalar(
        select(CandidateEvidence.id).where(
            CandidateEvidence.candidate_profile_id == profile.id,
            CandidateEvidence.source_type == EvidenceSourceType.PROFILE,
            CandidateEvidence.content == content,
            CandidateEvidence.source_resume_id == s.resume_id,
        )
    )
    if existing is not None:
        return existing
    evidence = await service.add_evidence(
        session,
        user,
        EvidenceIn(source_type=EvidenceSourceType.PROFILE, content=content),
        origin=_origin(s),
        source_resume_id=s.resume_id,
        commit=False,
    )
    return evidence.id


async def _apply(
    session: AsyncSession, user: User, s: ProfileSuggestion, data: dict[str, Any]
) -> uuid.UUID:
    """Apply ``data`` without committing; the caller commits everything at once."""
    if s.section == SuggestionSection.PERSONAL_INFO:
        patch = ProfilePatch.model_validate(data)
        return (await service.update_profile(session, user, patch, commit=False)).id

    if s.section in SECTION_SPECS:
        spec = SECTION_SPECS[s.section]
        if s.action == SuggestionAction.UPDATE:
            profile = await service.get_profile(session, user)
            item = await service.get_item(session, spec, profile, _require_target(s))
            merged = service.item_fields(spec, item) | data  # validate the result as a whole
            payload = spec.schema_in.model_validate(merged)
            replaced = await service.replace_item(
                session, user, spec, item.id, payload, commit=False
            )
            return replaced.id

        fields = dict(data)
        highlights = [h.strip() for h in fields.pop(HIGHLIGHTS, []) if h.strip()]
        created = await service.create_item(
            session, user, spec, spec.schema_in.model_validate(fields), commit=False
        )
        # One evidence row per claim. Entries without bullet points (e.g. a certification
        # line) use their verbatim source text as the single piece of evidence.
        claims = highlights
        if not claims and not s.proposed_data.get(HIGHLIGHTS) and s.source_excerpt:
            claims = [s.source_excerpt]
        for claim in claims:
            await service.add_evidence(
                session,
                user,
                EvidenceIn(
                    source_type=spec.evidence_type, subject_id=created.id, content=claim[:2000]
                ),
                origin=_origin(s),
                source_resume_id=s.resume_id,
                commit=False,
            )
        return created.id

    if s.section == SuggestionSection.SKILL:
        skill = await service.add_skill(session, user, SkillIn.model_validate(data), commit=False)
        if s.source_excerpt:  # e.g. "Languages: Python, Java" -> evidence for each skill
            evidence_id = await _profile_evidence(session, user, s, s.source_excerpt[:2000])
            await session.execute(
                insert(candidate_evidence_skills)
                .values(evidence_id=evidence_id, skill_id=skill.skill_id)
                .on_conflict_do_nothing()
            )
        return skill.id

    if s.action == SuggestionAction.CREATE:  # evidence
        evidence = await service.add_evidence(
            session,
            user,
            EvidenceIn.model_validate(data),
            origin=_origin(s),
            source_resume_id=s.resume_id,
            commit=False,
        )
        return evidence.id
    content = EvidenceUpdate.model_validate(data).content
    return (
        await service.update_evidence(session, user, _require_target(s), content, commit=False)
    ).id


async def accept_suggestion(
    session: AsyncSession,
    user: User,
    suggestion_id: uuid.UUID,
    edited_data: dict[str, Any] | None = None,
) -> SuggestionOut:
    """Apply a suggestion as an explicit user action, optionally with the user's edits.

    Everything (the profile change, any evidence rows, and the suggestion's status) is
    committed in one transaction, so it succeeds or fails as a whole. ``proposed_data``
    keeps what the AI proposed; ``accepted_data`` records what the candidate confirmed.
    """
    suggestion = await _get_pending(session, user, suggestion_id)
    data = suggestion.proposed_data if edited_data is None else edited_data
    try:
        if edited_data is not None:
            validate_proposal(suggestion.section, suggestion.action, edited_data)
        suggestion.status = SuggestionStatus.ACCEPTED
        suggestion.reviewed_at = datetime.now(UTC)
        suggestion.accepted_data = data
        suggestion.applied_target_id = await _apply(session, user, suggestion, data)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
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
