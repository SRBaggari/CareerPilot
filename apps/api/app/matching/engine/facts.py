"""The candidate facts structured checks use, loaded from the master profile.

Only **verified** evidence is included. Profile items (degrees, jobs, listed skills) are
user-provided facts; evidence rows are what a match may cite.
"""

import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.profiles.models import (
    EVIDENCE_SUBJECT_COLUMNS,
    CandidateEvidence,
    CandidateProfile,
    CandidateSkill,
    DegreeLevel,
    VerificationStatus,
)

DEGREE_RANK: dict[DegreeLevel, int] = {
    DegreeLevel.HIGH_SCHOOL: 1, DegreeLevel.CERTIFICATE: 2, DegreeLevel.DIPLOMA: 2,
    DegreeLevel.ASSOCIATE: 3, DegreeLevel.BACHELOR: 4, DegreeLevel.MASTER: 5,
    DegreeLevel.DOCTORATE: 6,
}  # fmt: skip


@dataclass(frozen=True)
class EducationFact:
    id: uuid.UUID
    institution: str
    degree: str | None
    degree_level: DegreeLevel | None
    end_date: date | None
    gpa: Decimal | None
    gpa_scale: Decimal | None

    @property
    def label(self) -> str:
        return f"{self.degree}, {self.institution}" if self.degree else self.institution


@dataclass
class CandidateFacts:
    profile_id: uuid.UUID
    skill_ids: set[uuid.UUID] = field(default_factory=set)
    skill_names: set[str] = field(default_factory=set)  # normalized
    certifications: dict[uuid.UUID, str] = field(default_factory=dict)
    educations: list[EducationFact] = field(default_factory=list)
    experience_years: float | None = None  # None: no dated work experience
    evidence_by_subject: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    verified_evidence_count: int = 0
    fingerprint: str = ""


def _merged_years(ranges: list[tuple[date, date]]) -> float:
    """Total years covered by possibly overlapping date ranges."""
    total_days = 0
    current_start: date | None = None
    current_end: date | None = None
    for start, end in sorted(ranges):
        if current_end is None or start > current_end:
            if current_start is not None and current_end is not None:
                total_days += (current_end - current_start).days
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_start is not None and current_end is not None:
        total_days += (current_end - current_start).days
    return round(total_days / 365.25, 1)


async def load_facts(session: AsyncSession, profile_id: uuid.UUID) -> CandidateFacts:
    profile = await session.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.id == profile_id)
        .options(
            selectinload(CandidateProfile.skills).selectinload(CandidateSkill.skill),
            selectinload(CandidateProfile.educations),
            selectinload(CandidateProfile.work_experiences),
            selectinload(CandidateProfile.certifications),
            selectinload(CandidateProfile.evidence),
        )
        .execution_options(populate_existing=True)
    )
    if profile is None:  # pragma: no cover - callers resolve the profile first
        raise LookupError("profile not found")

    facts = CandidateFacts(profile_id=profile.id)
    for candidate_skill in profile.skills:
        facts.skill_ids.add(candidate_skill.skill_id)
        facts.skill_names.add(candidate_skill.skill.normalized_name)
    facts.certifications = {c.id: c.name for c in profile.certifications}
    facts.educations = [
        EducationFact(e.id, e.institution, e.degree, e.degree_level, e.end_date, e.gpa, e.gpa_scale)
        for e in profile.educations
    ]

    today = datetime.now(UTC).date()
    ranges = []
    for job in profile.work_experiences:
        end = today if job.is_current else job.end_date
        if job.start_date and end and end >= job.start_date:
            ranges.append((job.start_date, end))
    facts.experience_years = _merged_years(ranges) if ranges else None

    verified = [e for e in profile.evidence if e.verification_status == VerificationStatus.VERIFIED]
    facts.verified_evidence_count = len(verified)
    by_subject: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for evidence in verified:
        column = EVIDENCE_SUBJECT_COLUMNS.get(evidence.source_type)
        subject = getattr(evidence, column) if column else None
        if subject is not None:
            by_subject[subject].append(evidence.id)
    facts.evidence_by_subject = dict(by_subject)
    facts.fingerprint = fingerprint(profile, verified)
    return facts


def fingerprint(profile: CandidateProfile, verified: list[CandidateEvidence]) -> str:
    """Changes whenever something matching reads changes. Built from content, not timestamps,
    so every relevant edit is detected (and irrelevant ones, like reordering, are not)."""
    parts = [f"e:{e.id}:{e.content}" for e in verified]
    parts += [f"s:{s.skill_id}" for s in profile.skills]
    parts += [
        f"d:{e.id}:{e.institution}:{e.degree}:{e.degree_level}:{e.end_date}:{e.gpa}:{e.gpa_scale}"
        for e in profile.educations
    ]
    parts += [
        f"w:{w.id}:{w.start_date}:{w.end_date}:{w.is_current}" for w in profile.work_experiences
    ]
    parts += [f"c:{c.id}:{c.name}" for c in profile.certifications]
    return hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()
