"""Resume ingestion pipeline.

Upload -> text extraction -> section detection -> structured extraction -> grounding
-> pending suggestions (with source excerpts and highlights) -> user review.

This module never writes the master profile. Extracted information only becomes part of
the profile when the candidate accepts a suggestion (``app.profiles.suggestions``).
"""

import hashlib
import re
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import ScalarResult, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIExecutionLog, AIExecutionStatus, AIOperation
from app.ai.provider import LLMError, LLMProvider
from app.core.config import Settings
from app.core.errors import ConflictError, FieldValidationError, NotFoundError
from app.profiles import service as profiles
from app.profiles.models import (
    CandidateProfile,
    CandidateSkill,
    ParseStatus,
    ProfileSuggestion,
    Resume,
    Skill,
    SuggestionAction,
    SuggestionSection,
    SuggestionSource,
    SuggestionStatus,
)
from app.profiles.schemas import ProfileIn, SkillIn
from app.profiles.suggestions import HIGHLIGHTS, MAX_HIGHLIGHTS, SECTION_SPECS, create_suggestion
from app.resumes.extraction import ResumeFileError, detect_format, extract_text
from app.resumes.grounding import ground, normalize
from app.resumes.heuristic import HeuristicResumeParser
from app.resumes.llm_parser import LLMResumeParser
from app.resumes.parsed import ParsedItem, ParsedResume
from app.resumes.schemas import ResumeDetail, ResumeOut
from app.resumes.storage import FileStorage
from app.users.models import User

RATIONALE = "Extracted from your uploaded resume. Check it before accepting."
MAX_WARNINGS = 50

# Fields that identify an entry, for skipping duplicates of existing/pending entries.
IDENTITY_FIELDS: dict[SuggestionSection, tuple[str, ...]] = {
    SuggestionSection.EDUCATION: ("institution", "degree"),
    SuggestionSection.WORK_EXPERIENCE: ("title", "company_name"),
    SuggestionSection.PROJECT: ("title",),
    SuggestionSection.CERTIFICATION: ("name",),
    SuggestionSection.ACHIEVEMENT: ("title",),
    SuggestionSection.COURSEWORK: ("course_name",),
}
PERSONAL_FIELDS = (
    "full_name", "contact_email", "phone", "location", "website_url", "linkedin_url",
    "github_url", "summary",
)  # fmt: skip
_DROPPABLE_ON_CROSS_FIELD_ERROR = ("start_date", "end_date", "issue_date", "expiration_date",
                                   "gpa", "gpa_scale", "is_current")  # fmt: skip


def _identity(section: SuggestionSection, data: dict[str, Any]) -> tuple[str, ...]:
    return tuple(normalize(str(data.get(f) or "")) for f in IDENTITY_FIELDS[section])


def safe_filename(name: str | None) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (base or "resume")[-255:]


# --- Parsing ----------------------------------------------------------------------------


async def _parse(
    session: AsyncSession,
    user: User,
    text: str,
    settings: Settings,
    llm: LLMProvider | None,
) -> tuple[ParsedResume, str, uuid.UUID | None, list[str]]:
    """Run the configured parser. Returns (result, parser name, AI log id, warnings)."""
    warnings: list[str] = []
    if settings.resume_parser != "heuristic" and llm is not None:
        parser = LLMResumeParser(llm)
        log = AIExecutionLog(
            user_id=user.id,
            operation=AIOperation.RESUME_PARSING,
            provider=llm.name,
            model=llm.model,
            prompt_version="resume-extraction-v1",
        )
        try:
            parsed, result = await parser.parse(text)
        except LLMError as exc:
            log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
            session.add(log)
            warnings.append(f"AI parsing failed ({exc}); the rule-based parser was used instead.")
        else:
            log.status = AIExecutionStatus.SUCCESS
            log.model = result.model
            log.input_tokens, log.output_tokens = result.input_tokens, result.output_tokens
            log.latency_ms = result.latency_ms
            session.add(log)
            await session.flush()
            return parsed, parser.name, log.id, warnings
    elif settings.resume_parser == "llm":
        warnings.append(
            "The AI parser is not configured (set ANTHROPIC_API_KEY); the rule-based parser "
            "was used instead."
        )
    heuristic = HeuristicResumeParser()
    return heuristic.parse(text), heuristic.name, None, warnings


# --- Suggestions ------------------------------------------------------------------------


def _validated_item(item: ParsedItem) -> dict[str, Any] | None:
    """Validate against the section's schema. Fields that fail validation are dropped
    (never "fixed"), retrying once; returns None if the entry is still invalid."""
    schema = SECTION_SPECS[item.section].schema_in
    data = dict(item.data)
    for _ in range(2):
        try:
            validated = schema.model_validate(data)
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e["loc"]}
            if any(not e["loc"] for e in exc.errors()):  # a cross-field rule failed
                bad |= set(_DROPPABLE_ON_CROSS_FIELD_ERROR)
            required = {n for n, f in schema.model_fields.items() if f.is_required()}
            if bad & required or not bad & set(data):
                return None
            data = {k: v for k, v in data.items() if k not in bad}
            continue
        return validated.model_dump(mode="json", exclude_defaults=True)
    return None


async def _existing_identities(
    session: AsyncSession, profile: CandidateProfile
) -> set[tuple[SuggestionSection, tuple[str, ...]]]:
    seen: set[tuple[SuggestionSection, tuple[str, ...]]] = set()
    for section, spec in SECTION_SPECS.items():
        rows: ScalarResult[Any] = await session.scalars(
            select(spec.model).where(spec.model.candidate_profile_id == profile.id)
        )
        for row in rows:
            data = {f: getattr(row, f) for f in IDENTITY_FIELDS[section]}
            seen.add((section, _identity(section, data)))
    previous = await session.scalars(
        select(ProfileSuggestion).where(
            ProfileSuggestion.candidate_profile_id == profile.id,
            ProfileSuggestion.section.in_(list(SECTION_SPECS)),
        )
    )
    # Any earlier suggestion counts, whatever its status: accepted ones may have been edited
    # (so their identity differs in the profile) and rejected ones must not come back.
    for s in previous:
        seen.add((s.section, _identity(s.section, s.proposed_data)))
    return seen


async def _existing_skill_names(session: AsyncSession, profile: CandidateProfile) -> set[str]:
    names = set(
        await session.scalars(
            select(Skill.normalized_name)
            .join(CandidateSkill, CandidateSkill.skill_id == Skill.id)
            .where(CandidateSkill.candidate_profile_id == profile.id)
        )
    )
    previous: ScalarResult[dict[str, Any]] = await session.scalars(
        select(ProfileSuggestion.proposed_data).where(
            ProfileSuggestion.candidate_profile_id == profile.id,
            ProfileSuggestion.section == SuggestionSection.SKILL,
        )
    )
    names |= {profiles.normalize_skill_name(str(d.get("name", ""))) for d in previous}
    return names


def _personal_changes(profile: CandidateProfile, personal: dict[str, Any]) -> dict[str, Any]:
    """Only fill fields the candidate hasn't set (and a differing name); never overwrite."""
    changes: dict[str, Any] = {}
    for field in PERSONAL_FIELDS:
        value = personal.get(field)
        if not value:
            continue
        if field == "full_name":
            if normalize(str(value)) == normalize(profile.full_name):
                continue
        elif getattr(profile, field):
            continue
        try:  # validate (and normalize) each field on its own
            probe = ProfileIn.model_validate({"full_name": "-", field: value})
        except ValidationError:
            continue
        changes[field] = getattr(probe, field) if field != "full_name" else value
    return changes


async def _create_suggestions(
    session: AsyncSession,
    profile: CandidateProfile,
    resume: Resume,
    parsed: ParsedResume,
    log_id: uuid.UUID | None,
) -> list[str]:
    warnings: list[str] = []
    common: dict[str, Any] = {
        "source": SuggestionSource.RESUME_EXTRACTION,
        "rationale": RATIONALE,
        "resume_id": resume.id,
        "ai_execution_log_id": log_id,
        "commit": False,
    }
    changes = _personal_changes(profile, parsed.personal)
    previous_personal: ScalarResult[dict[str, Any]] = await session.scalars(
        select(ProfileSuggestion.proposed_data).where(
            ProfileSuggestion.candidate_profile_id == profile.id,
            ProfileSuggestion.section == SuggestionSection.PERSONAL_INFO,
        )
    )
    for proposed in previous_personal:  # don't re-propose values already proposed before
        changes = {k: v for k, v in changes.items() if proposed.get(k) != v}
    if changes:
        await create_suggestion(
            session, profile, section=SuggestionSection.PERSONAL_INFO,
            action=SuggestionAction.UPDATE, proposed_data=changes, **common,
        )  # fmt: skip

    seen = await _existing_identities(session, profile)
    for item in parsed.items:
        data = _validated_item(item)
        if data is None:
            warnings.append(
                f"Couldn't use an extracted {item.section.value.replace('_', ' ')}: "
                f"{(item.source_excerpt or str(item.data))[:120]!r}"
            )
            continue
        identity = (item.section, _identity(item.section, data))
        if identity in seen:
            continue  # already in the profile or awaiting review
        seen.add(identity)
        highlights = [h.strip()[:2000] for h in item.highlights if h.strip()][:MAX_HIGHLIGHTS]
        if highlights:
            data[HIGHLIGHTS] = highlights
        await create_suggestion(
            session, profile, section=item.section, action=SuggestionAction.CREATE,
            proposed_data=data, source_excerpt=item.source_excerpt[:5000], **common,
        )  # fmt: skip

    known_skills = await _existing_skill_names(session, profile)
    for skill in parsed.skills:
        key = profiles.normalize_skill_name(skill.name)
        if key in known_skills:
            continue
        try:
            payload = SkillIn.model_validate({"name": skill.name, "category": skill.category})
        except ValidationError:
            try:  # e.g. an unknown category: keep the skill, drop the category
                payload = SkillIn.model_validate({"name": skill.name})
            except ValidationError:
                continue
        known_skills.add(key)
        await create_suggestion(
            session, profile, section=SuggestionSection.SKILL, action=SuggestionAction.CREATE,
            proposed_data=payload.model_dump(mode="json", exclude_none=True),
            source_excerpt=skill.source_excerpt[:2000], **common,
        )  # fmt: skip
    return warnings


# --- Public API -------------------------------------------------------------------------


async def _counts(
    session: AsyncSession, resume_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    rows = await session.execute(
        select(
            ProfileSuggestion.resume_id,
            func.count().filter(ProfileSuggestion.status == SuggestionStatus.PENDING),
            func.count(),
        )
        .where(ProfileSuggestion.resume_id.in_(resume_ids))
        .group_by(ProfileSuggestion.resume_id)
    )
    return {rid: (pending, total) for rid, pending, total in rows if rid is not None}


def _out(resume: Resume, counts: tuple[int, int]) -> dict[str, Any]:
    return {
        "id": resume.id, "file_name": resume.file_name, "file_format": resume.file_format,
        "file_size_bytes": resume.file_size_bytes, "parse_status": resume.parse_status,
        "parse_error": resume.parse_error, "parse_warnings": resume.parse_warnings,
        "parser_name": resume.parser_name, "is_primary": resume.is_primary,
        "pending_suggestions": counts[0], "total_suggestions": counts[1],
        "created_at": resume.created_at,
    }  # fmt: skip


async def _get(session: AsyncSession, user: User, resume_id: uuid.UUID) -> Resume:
    profile = await profiles.get_profile(session, user)
    resume = await session.scalar(
        select(Resume).where(Resume.id == resume_id, Resume.candidate_profile_id == profile.id)
    )
    if resume is None:
        raise NotFoundError("Resume not found.")
    return resume


async def get_resume(session: AsyncSession, user: User, resume_id: uuid.UUID) -> ResumeDetail:
    resume = await _get(session, user, resume_id)
    counts = (await _counts(session, [resume.id])).get(resume.id, (0, 0))
    return ResumeDetail(**_out(resume, counts), parsed_text=resume.parsed_text)


async def list_resumes(session: AsyncSession, user: User) -> list[ResumeOut]:
    profile = await profiles.get_profile(session, user)
    resumes = list(
        await session.scalars(
            select(Resume)
            .where(Resume.candidate_profile_id == profile.id)
            .order_by(Resume.created_at.desc())
        )
    )
    counts = await _counts(session, [r.id for r in resumes])
    return [ResumeOut(**_out(r, counts.get(r.id, (0, 0)))) for r in resumes]


async def ingest_resume(
    session: AsyncSession,
    user: User,
    *,
    filename: str | None,
    data: bytes,
    settings: Settings,
    storage: FileStorage,
    llm: LLMProvider | None,
) -> ResumeDetail:
    profile = await profiles.get_profile(session, user)
    if not data:
        raise FieldValidationError("file", "The file is empty.")
    if len(data) > settings.max_resume_bytes:
        limit_mb = settings.max_resume_bytes / (1024 * 1024)
        raise FieldValidationError("file", f"The file is larger than {limit_mb:g} MB.")
    name = safe_filename(filename)
    try:
        file_format = detect_format(data, name)
    except ResumeFileError as exc:
        raise FieldValidationError("file", str(exc)) from exc

    digest = hashlib.sha256(data).hexdigest()
    existing = await session.scalar(
        select(Resume.id).where(Resume.candidate_profile_id == profile.id, Resume.sha256 == digest)
    )
    if existing is not None:
        raise ConflictError("You have already uploaded this file.")
    has_primary = await session.scalar(
        select(Resume.id).where(Resume.candidate_profile_id == profile.id, Resume.is_primary)
    )

    resume = Resume(
        candidate_profile_id=profile.id,
        file_name=name,
        file_format=file_format,
        storage_key=f"resumes/{profile.id}/{uuid.uuid4().hex}.{file_format.value}",
        file_size_bytes=len(data),
        sha256=digest,
        is_primary=has_primary is None,
    )
    session.add(resume)
    await session.flush()
    storage.save(resume.storage_key, data)
    try:
        try:
            extracted = extract_text(data, file_format)
        except ResumeFileError as exc:
            resume.parse_status, resume.parse_error = ParseStatus.FAILED, str(exc)
        else:
            resume.parsed_text = extracted.text
            parsed, parser_name, log_id, warnings = await _parse(
                session, user, extracted.text, settings, llm
            )
            grounded = ground(parsed, extracted.text)
            warnings += grounded.warnings
            warnings += await _create_suggestions(session, profile, resume, grounded, log_id)
            resume.parser_name = parser_name
            resume.parse_warnings = [w[:300] for w in warnings][:MAX_WARNINGS]
            resume.parse_status = ParseStatus.PARSED
        await session.commit()
    except BaseException:
        await session.rollback()
        storage.delete(resume.storage_key)
        raise
    return await get_resume(session, user, resume.id)


async def delete_resume(
    session: AsyncSession, user: User, resume_id: uuid.UUID, storage: FileStorage
) -> None:
    """Delete the upload and its pending suggestions. Accepted information stays in the
    profile (its evidence keeps pointing at nothing via ON DELETE SET NULL)."""
    resume = await _get(session, user, resume_id)
    pending = await session.scalars(
        select(ProfileSuggestion).where(
            ProfileSuggestion.resume_id == resume.id,
            ProfileSuggestion.status == SuggestionStatus.PENDING,
        )
    )
    for suggestion in pending:
        await session.delete(suggestion)
    key = resume.storage_key
    await session.delete(resume)
    await session.commit()
    storage.delete(key)
