"""Candidate profile service: the only code path that writes the master profile.

Everything written here is a user-provided fact. AI output never calls these functions
directly; it goes through ``app.profiles.suggestions`` and is applied only when the candidate
accepts it.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import ScalarResult, func, inspect, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, FieldValidationError, NotFoundError
from app.documents.models import generated_claim_evidence
from app.profiles.models import (
    EVIDENCE_SUBJECT_COLUMNS,
    Achievement,
    CandidateEvidence,
    CandidateProfile,
    CandidateSkill,
    Certification,
    Coursework,
    Education,
    EvidenceOrigin,
    EvidenceSourceType,
    ProfileSuggestion,
    Project,
    Skill,
    SuggestionStatus,
    WorkExperience,
)
from app.profiles.schemas import (
    AchievementIn,
    AchievementOut,
    CertificationIn,
    CertificationOut,
    CourseworkIn,
    CourseworkOut,
    EducationIn,
    EducationOut,
    EvidenceIn,
    EvidenceOut,
    ItemOutMixin,
    ProfileIn,
    ProfileOut,
    ProfilePatch,
    ProjectIn,
    ProjectOut,
    SkillIn,
    SkillOut,
    SkillUpdate,
    WorkExperienceIn,
    WorkExperienceOut,
)
from app.users.models import User


@dataclass(frozen=True)
class SectionSpec[M: (Education, WorkExperience, Project, Certification, Achievement, Coursework)]:
    """Binds a profile section's URL segment, ORM model, schemas, and evidence type."""

    path: str
    label: str
    model: type[M]
    schema_in: type[BaseModel]
    schema_out: type[ItemOutMixin]
    evidence_type: EvidenceSourceType
    profile_attr: str  # attribute on CandidateProfile holding the items


SECTIONS: dict[str, SectionSpec[Any]] = {
    spec.path: spec
    for spec in (
        SectionSpec(
            "educations", "Education", Education, EducationIn, EducationOut,
            EvidenceSourceType.EDUCATION, "educations",
        ),
        SectionSpec(
            "work-experiences", "Work experience", WorkExperience, WorkExperienceIn,
            WorkExperienceOut, EvidenceSourceType.WORK_EXPERIENCE, "work_experiences",
        ),
        SectionSpec(
            "projects", "Project", Project, ProjectIn, ProjectOut,
            EvidenceSourceType.PROJECT, "projects",
        ),
        SectionSpec(
            "certifications", "Certification", Certification, CertificationIn,
            CertificationOut, EvidenceSourceType.CERTIFICATION, "certifications",
        ),
        SectionSpec(
            "achievements", "Achievement", Achievement, AchievementIn, AchievementOut,
            EvidenceSourceType.ACHIEVEMENT, "achievements",
        ),
        SectionSpec(
            "coursework", "Coursework", Coursework, CourseworkIn, CourseworkOut,
            EvidenceSourceType.COURSEWORK, "coursework",
        ),
    )
}  # fmt: skip
SECTION_BY_EVIDENCE_TYPE = {spec.evidence_type: spec for spec in SECTIONS.values()}

PROFILE_FIELDS = tuple(ProfileIn.model_fields)
REQUIRED_PROFILE_FIELDS = {n for n, f in ProfileIn.model_fields.items() if f.is_required()}


def _now() -> datetime:
    return datetime.now(UTC)


async def _save(session: AsyncSession, commit: bool) -> None:
    """Commit, or only flush when the caller is composing a larger transaction
    (e.g. accepting a suggestion must apply all of its changes or none)."""
    if commit:
        await session.commit()
    else:
        await session.flush()


# --- Loading ---------------------------------------------------------------------------


async def get_profile(session: AsyncSession, user: User) -> CandidateProfile:
    profile = await session.scalar(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    )
    if profile is None:
        raise NotFoundError("Profile not found. Create your profile first.")
    return profile


async def _cited_evidence_ids(session: AsyncSession, profile_id: uuid.UUID) -> set[uuid.UUID]:
    rows: ScalarResult[uuid.UUID] = await session.scalars(
        select(generated_claim_evidence.c.evidence_id)
        .join(CandidateEvidence, CandidateEvidence.id == generated_claim_evidence.c.evidence_id)
        .where(CandidateEvidence.candidate_profile_id == profile_id)
        .distinct()
    )
    return set(rows)


def _subject_id(evidence: CandidateEvidence) -> uuid.UUID | None:
    column = EVIDENCE_SUBJECT_COLUMNS.get(evidence.source_type)
    return getattr(evidence, column) if column else None


def evidence_out(evidence: CandidateEvidence, cited: set[uuid.UUID]) -> EvidenceOut:
    return EvidenceOut(
        id=evidence.id,
        source_type=evidence.source_type,
        subject_id=_subject_id(evidence),
        content=evidence.content,
        origin=evidence.origin,
        confirmed_at=evidence.confirmed_at,
        is_cited=evidence.id in cited,
        created_at=evidence.created_at,
        updated_at=evidence.updated_at,
    )


def _columns(row: Any, schema: type[BaseModel]) -> dict[str, Any]:
    """Values of ``row``'s mapped columns that ``schema`` declares.

    Only column attributes are read: touching a relationship would trigger a lazy load,
    which is not allowed under asyncio.
    """
    columns = {attr.key for attr in inspect(type(row)).column_attrs}
    return {name: getattr(row, name) for name in schema.model_fields if name in columns}


def item_out(spec: SectionSpec[Any], item: Any, evidence: list[EvidenceOut]) -> ItemOutMixin:
    data = _columns(item, spec.schema_out)
    data["evidence"] = evidence
    return spec.schema_out.model_validate(data)


def skill_out(candidate_skill: CandidateSkill) -> SkillOut:
    return SkillOut(
        id=candidate_skill.id,
        skill_id=candidate_skill.skill_id,
        name=candidate_skill.skill.name,
        category=candidate_skill.skill.category,
        proficiency=candidate_skill.proficiency,
        years_experience=candidate_skill.years_experience,
    )


def _sorted(items: list[Any]) -> list[Any]:
    return sorted(items, key=lambda i: (i.sort_order, i.created_at))


async def read_profile(session: AsyncSession, user: User) -> ProfileOut:
    profile = await session.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.user_id == user.id)
        .options(
            *(selectinload(getattr(CandidateProfile, s.profile_attr)) for s in SECTIONS.values()),
            selectinload(CandidateProfile.skills).selectinload(CandidateSkill.skill),
            selectinload(CandidateProfile.evidence),
        )
        .execution_options(populate_existing=True)
    )
    if profile is None:
        raise NotFoundError("Profile not found. Create your profile first.")

    cited = await _cited_evidence_ids(session, profile.id)
    by_subject: dict[uuid.UUID | None, list[EvidenceOut]] = defaultdict(list)
    for ev in sorted(profile.evidence, key=lambda e: e.created_at):
        by_subject[_subject_id(ev)].append(evidence_out(ev, cited))

    pending = await session.scalar(
        select(func.count())
        .select_from(ProfileSuggestion)
        .where(
            ProfileSuggestion.candidate_profile_id == profile.id,
            ProfileSuggestion.status == SuggestionStatus.PENDING,
        )
    )
    data: dict[str, Any] = _columns(profile, ProfileOut)
    for spec in SECTIONS.values():
        items = _sorted(getattr(profile, spec.profile_attr))
        data[spec.profile_attr] = [item_out(spec, i, by_subject[i.id]) for i in items]
    data["skills"] = [
        skill_out(s) for s in sorted(profile.skills, key=lambda s: s.skill.normalized_name)
    ]
    data["evidence"] = by_subject[None]
    data["pending_suggestions"] = pending or 0
    return ProfileOut.model_validate(data)


# --- Profile ----------------------------------------------------------------------------


async def create_profile(session: AsyncSession, user: User, payload: ProfileIn) -> ProfileOut:
    exists = await session.scalar(
        select(CandidateProfile.id).where(CandidateProfile.user_id == user.id)
    )
    if exists is not None:
        raise ConflictError("A profile already exists for this user.")
    session.add(CandidateProfile(user_id=user.id, **payload.model_dump(mode="json")))
    await session.commit()
    return await read_profile(session, user)


async def update_profile(
    session: AsyncSession, user: User, patch: ProfilePatch, *, commit: bool = True
) -> ProfileOut:
    profile = await get_profile(session, user)
    changes = patch.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is None and field in REQUIRED_PROFILE_FIELDS:
            raise FieldValidationError(field, f"{field} is required and cannot be cleared")
    merged = {name: getattr(profile, name) for name in PROFILE_FIELDS} | changes
    validated = ProfileIn.model_validate(merged)  # whole-object validation
    for field, value in validated.model_dump(mode="json").items():
        setattr(profile, field, value)
    await _save(session, commit)
    return await read_profile(session, user)


async def delete_profile(session: AsyncSession, user: User) -> None:
    profile = await get_profile(session, user)
    await session.delete(profile)  # DB cascades remove every section, evidence, document
    await session.commit()


# --- Section items ----------------------------------------------------------------------


async def get_item[M: (Education, WorkExperience, Project, Certification, Achievement, Coursework)](
    session: AsyncSession, spec: SectionSpec[M], profile: CandidateProfile, item_id: uuid.UUID
) -> M:
    item = await session.scalar(
        select(spec.model).where(
            spec.model.id == item_id, spec.model.candidate_profile_id == profile.id
        )
    )
    if item is None:
        raise NotFoundError(f"{spec.label} not found.")
    return item


async def _validate_references(
    session: AsyncSession, profile: CandidateProfile, payload: BaseModel
) -> None:
    education_id = getattr(payload, "education_id", None)
    if education_id is not None:
        owned = await session.scalar(
            select(Education.id).where(
                Education.id == education_id, Education.candidate_profile_id == profile.id
            )
        )
        if owned is None:
            raise FieldValidationError("education_id", "education entry not found")


async def _item_evidence(
    session: AsyncSession, spec: SectionSpec[Any], profile: CandidateProfile, item_id: uuid.UUID
) -> list[EvidenceOut]:
    column = getattr(CandidateEvidence, EVIDENCE_SUBJECT_COLUMNS[spec.evidence_type])
    rows = await session.scalars(
        select(CandidateEvidence).where(column == item_id).order_by(CandidateEvidence.created_at)
    )
    cited = await _cited_evidence_ids(session, profile.id)
    return [evidence_out(ev, cited) for ev in rows]


async def create_item(
    session: AsyncSession,
    user: User,
    spec: SectionSpec[Any],
    payload: BaseModel,
    *,
    commit: bool = True,
) -> ItemOutMixin:
    profile = await get_profile(session, user)
    await _validate_references(session, profile, payload)
    item = spec.model(candidate_profile_id=profile.id, **payload.model_dump())
    session.add(item)
    await _save(session, commit)
    await session.refresh(item)
    return item_out(spec, item, [])


async def replace_item(
    session: AsyncSession,
    user: User,
    spec: SectionSpec[Any],
    item_id: uuid.UUID,
    payload: BaseModel,
    *,
    commit: bool = True,
) -> ItemOutMixin:
    profile = await get_profile(session, user)
    item = await get_item(session, spec, profile, item_id)
    await _validate_references(session, profile, payload)
    for field, value in payload.model_dump().items():
        setattr(item, field, value)
    await _save(session, commit)
    await session.refresh(item)
    return item_out(spec, item, await _item_evidence(session, spec, profile, item.id))


async def delete_item(
    session: AsyncSession, user: User, spec: SectionSpec[Any], item_id: uuid.UUID
) -> None:
    profile = await get_profile(session, user)
    item = await get_item(session, spec, profile, item_id)
    evidence = await _item_evidence(session, spec, profile, item.id)
    if any(ev.is_cited for ev in evidence):
        raise ConflictError(
            f"This {spec.label.lower()} has evidence cited by a generated document. "
            "Remove or regenerate that document first."
        )
    await session.delete(item)  # its evidence is removed by the DB cascade
    await session.commit()


def item_fields(spec: SectionSpec[Any], item: Any) -> dict[str, Any]:
    """Current values of an item in its input-schema shape (used to merge suggestions)."""
    return _columns(item, spec.schema_in)


# --- Evidence ---------------------------------------------------------------------------


async def _get_evidence(
    session: AsyncSession, profile: CandidateProfile, evidence_id: uuid.UUID
) -> CandidateEvidence:
    evidence = await session.scalar(
        select(CandidateEvidence).where(
            CandidateEvidence.id == evidence_id,
            CandidateEvidence.candidate_profile_id == profile.id,
        )
    )
    if evidence is None:
        raise NotFoundError("Evidence not found.")
    return evidence


async def add_evidence(
    session: AsyncSession,
    user: User,
    payload: EvidenceIn,
    *,
    origin: EvidenceOrigin = EvidenceOrigin.USER_ENTERED,
    source_resume_id: uuid.UUID | None = None,
    commit: bool = True,
) -> EvidenceOut:
    """Add a fact. ``origin``/``source_resume_id`` are only set when applying an accepted
    suggestion; in every case the candidate has confirmed the content (``confirmed_at``)."""
    profile = await get_profile(session, user)
    subject: dict[str, uuid.UUID] = {}
    if payload.source_type != EvidenceSourceType.PROFILE:
        if payload.subject_id is None:  # also enforced by EvidenceIn
            raise FieldValidationError("subject_id", "subject_id is required")
        spec = SECTION_BY_EVIDENCE_TYPE[payload.source_type]
        try:
            await get_item(session, spec, profile, payload.subject_id)
        except NotFoundError as exc:
            raise FieldValidationError("subject_id", f"{spec.label} not found") from exc
        subject[EVIDENCE_SUBJECT_COLUMNS[payload.source_type]] = payload.subject_id

    evidence = CandidateEvidence(
        candidate_profile_id=profile.id,
        source_type=payload.source_type,
        origin=origin,
        content=payload.content,
        confirmed_at=_now(),
        source_resume_id=source_resume_id,
        **subject,
    )
    session.add(evidence)
    await _save(session, commit)
    await session.refresh(evidence)
    return evidence_out(evidence, set())


async def update_evidence(
    session: AsyncSession,
    user: User,
    evidence_id: uuid.UUID,
    content: str,
    *,
    commit: bool = True,
) -> EvidenceOut:
    profile = await get_profile(session, user)
    evidence = await _get_evidence(session, profile, evidence_id)
    cited = await _cited_evidence_ids(session, profile.id)
    if evidence.id in cited and evidence.content != content:
        raise ConflictError(
            "This evidence is cited by a generated document; editing it would make that "
            "document's claims untraceable. Add new evidence instead."
        )
    evidence.content = content
    await _save(session, commit)
    await session.refresh(evidence)
    return evidence_out(evidence, cited)


async def delete_evidence(session: AsyncSession, user: User, evidence_id: uuid.UUID) -> None:
    profile = await get_profile(session, user)
    evidence = await _get_evidence(session, profile, evidence_id)
    if evidence.id in await _cited_evidence_ids(session, profile.id):
        raise ConflictError("This evidence is cited by a generated document and cannot be deleted.")
    await session.delete(evidence)
    await session.commit()


# --- Skills -----------------------------------------------------------------------------


def normalize_skill_name(name: str) -> str:
    return " ".join(name.lower().split())


async def add_skill(
    session: AsyncSession, user: User, payload: SkillIn, *, commit: bool = True
) -> SkillOut:
    profile = await get_profile(session, user)
    normalized = normalize_skill_name(payload.name)
    # The skill vocabulary is shared: reuse an existing entry, never rename it.
    await session.execute(
        insert(Skill)
        .values(name=payload.name, normalized_name=normalized, category=payload.category)
        .on_conflict_do_nothing(index_elements=["normalized_name"])
    )
    skill = await session.scalar(select(Skill).where(Skill.normalized_name == normalized))
    if skill is None:  # pragma: no cover - the upsert above guarantees a row
        raise ConflictError("Could not register the skill; please retry.")
    if skill.category is None and payload.category is not None:
        skill.category = payload.category

    duplicate = await session.scalar(
        select(CandidateSkill.id).where(
            CandidateSkill.candidate_profile_id == profile.id, CandidateSkill.skill_id == skill.id
        )
    )
    if duplicate is not None:
        raise ConflictError(f"'{skill.name}' is already in your skills.")
    candidate_skill = CandidateSkill(
        candidate_profile_id=profile.id,
        skill=skill,
        proficiency=payload.proficiency,
        years_experience=payload.years_experience,
    )
    session.add(candidate_skill)
    await _save(session, commit)
    return skill_out(candidate_skill)


async def _get_candidate_skill(
    session: AsyncSession, profile: CandidateProfile, candidate_skill_id: uuid.UUID
) -> CandidateSkill:
    candidate_skill = await session.scalar(
        select(CandidateSkill)
        .where(
            CandidateSkill.id == candidate_skill_id,
            CandidateSkill.candidate_profile_id == profile.id,
        )
        .options(selectinload(CandidateSkill.skill))
    )
    if candidate_skill is None:
        raise NotFoundError("Skill not found.")
    return candidate_skill


async def update_skill(
    session: AsyncSession, user: User, candidate_skill_id: uuid.UUID, payload: SkillUpdate
) -> SkillOut:
    profile = await get_profile(session, user)
    candidate_skill = await _get_candidate_skill(session, profile, candidate_skill_id)
    candidate_skill.proficiency = payload.proficiency
    candidate_skill.years_experience = payload.years_experience
    await session.commit()
    return skill_out(candidate_skill)


async def delete_skill(session: AsyncSession, user: User, candidate_skill_id: uuid.UUID) -> None:
    profile = await get_profile(session, user)
    candidate_skill = await _get_candidate_skill(session, profile, candidate_skill_id)
    await session.delete(candidate_skill)  # the shared Skill entry is kept
    await session.commit()
