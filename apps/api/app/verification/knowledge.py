"""What the engine knows about the candidate: verified evidence and stored profile records.

Also the profile checks: record facts in a document must equal the stored records, and a
claim that conflicts with stored information (a date outside an item's range, more years
of experience than the work history holds, a different degree, GPA or role) is
CONTRADICTED.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.documents.models import VerificationVerdict
from app.profiles.models import (
    EVIDENCE_SUBJECT_COLUMNS,
    CandidateEvidence,
    CandidateProfile,
    DegreeLevel,
    VerificationStatus,
)
from app.retrieval.service import record_label, with_sources
from app.verification.compare import EvidenceText
from app.verification.types import RECORD_BOUND_TYPES, ClaimInput, ClaimType

V = VerificationVerdict


@dataclass(frozen=True)
class Evidence:
    id: uuid.UUID
    content: str
    context: str  # the item it belongs to, e.g. "Machine Learning Intern at Acme"
    subject_id: uuid.UUID | None

    @property
    def text(self) -> EvidenceText:
        return EvidenceText(self.content, self.context)


@dataclass(frozen=True)
class ProfileCheck:
    verdict: VerificationVerdict
    reason: str


@dataclass
class CandidateKnowledge:
    profile: CandidateProfile
    verified: dict[uuid.UUID, Evidence] = field(default_factory=dict)
    unverified: dict[uuid.UUID, Evidence] = field(default_factory=dict)
    today: date = field(default_factory=lambda: datetime.now(UTC).date())

    def scope(self, claim: ClaimInput) -> dict[uuid.UUID, Evidence]:
        """The evidence a claim may rest on: a bullet only its own item's evidence."""
        if claim.claim_type in RECORD_BOUND_TYPES and claim.record_id is not None:
            return {i: e for i, e in self.verified.items() if e.subject_id == claim.record_id}
        return self.verified

    # --- Records ------------------------------------------------------------------------

    def records(self, claim_type: ClaimType) -> dict[uuid.UUID, Any]:
        p = self.profile
        by_type: dict[ClaimType, list[Any]] = {
            ClaimType.EMPLOYMENT: p.work_experiences,
            ClaimType.PROJECT_ENTRY: p.projects,
            ClaimType.EDUCATION: p.educations,
            ClaimType.CERTIFICATION: p.certifications,
            ClaimType.ACHIEVEMENT: p.achievements,
            ClaimType.COURSEWORK: p.coursework,
            ClaimType.EXPERIENCE: p.work_experiences,
            ClaimType.PROJECT: p.projects,
        }
        return {r.id: r for r in by_type.get(claim_type, [])}

    def total_experience_years(self) -> float | None:
        """Work history length in years, overlapping roles counted once."""
        spans = sorted(
            (j.start_date, j.end_date or (self.today if j.is_current else j.start_date))
            for j in self.profile.work_experiences
            if j.start_date is not None
        )
        if not spans:
            return None
        days, current_start, current_end = 0, spans[0][0], spans[0][1]
        for start, end in spans[1:]:
            if start > current_end:
                days += (current_end - current_start).days
                current_start, current_end = start, end
            else:
                current_end = max(current_end, end)
        days += (current_end - current_start).days
        return days / 365.25


# --- Loading ----------------------------------------------------------------------------


async def load_knowledge(session: AsyncSession, profile_id: uuid.UUID) -> CandidateKnowledge:
    profile = await session.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.id == profile_id)
        .options(
            selectinload(CandidateProfile.work_experiences),
            selectinload(CandidateProfile.projects),
            selectinload(CandidateProfile.educations),
            selectinload(CandidateProfile.certifications),
            selectinload(CandidateProfile.achievements),
            selectinload(CandidateProfile.coursework),
        )
        .execution_options(populate_existing=True)
    )
    if profile is None:  # pragma: no cover - resolved by the caller
        raise LookupError("profile not found")
    knowledge = CandidateKnowledge(profile=profile)
    rows = await session.scalars(
        with_sources(
            select(CandidateEvidence).where(CandidateEvidence.candidate_profile_id == profile.id)
        )
    )
    for row in rows:
        column = EVIDENCE_SUBJECT_COLUMNS.get(row.source_type)
        item = Evidence(
            row.id, row.content, record_label(row) or "", getattr(row, column) if column else None
        )
        if row.verification_status == VerificationStatus.VERIFIED:
            knowledge.verified[row.id] = item
        else:
            knowledge.unverified[row.id] = item
    return knowledge


# --- Record facts -----------------------------------------------------------------------

_RECORD_FIELDS: dict[ClaimType, tuple[str, ...]] = {
    ClaimType.EMPLOYMENT: ("title", "company_name", "location", "start_date", "end_date",
                           "is_current"),
    ClaimType.PROJECT_ENTRY: ("title", "role", "start_date", "end_date"),
    ClaimType.EDUCATION: ("institution", "degree", "field_of_study", "start_date", "end_date",
                          "gpa", "gpa_scale"),
    ClaimType.CERTIFICATION: ("name", "issuer", "issue_date"),
    ClaimType.ACHIEVEMENT: ("title", "achieved_on"),
    ClaimType.COURSEWORK: ("course_name",),
}  # fmt: skip
_HEADER_FIELDS = ("full_name", "headline", "contact_email", "phone", "location", "website_url",
                  "linkedin_url", "github_url")  # fmt: skip
_LABELS = {
    "company_name": "employer",
    "full_name": "name",
    "course_name": "course",
    "achieved_on": "date",
    "issue_date": "issue date",
    "is_current": "current role",
    "field_of_study": "field of study",
    "gpa_scale": "GPA scale",
    "contact_email": "email",
    "start_date": "start date",
    "end_date": "end date",
}


def _same(stated: Any, stored: Any) -> bool:
    if stated is None or stated == "":
        return stored is None or stored == ""
    if isinstance(stored, date):
        return str(stated) == stored.isoformat()
    if isinstance(stored, Decimal):
        try:
            return Decimal(str(stated)) == stored
        except ArithmeticError:
            return False
    if isinstance(stored, str) and isinstance(stated, str):
        return stated.strip() == stored.strip()
    return bool(stated == stored)


def _shown(value: Any) -> str:
    if value is None or value == "":
        return "(blank)"
    return str(value)


def check_record_fact(claim: ClaimInput, knowledge: CandidateKnowledge) -> ProfileCheck:
    """A record fact must be one of the candidate's records, stated exactly as stored."""
    stated = claim.facts or {}
    if claim.claim_type == ClaimType.CONTACT:
        record: Any = knowledge.profile
        fields: tuple[str, ...] = _HEADER_FIELDS
        kind = "profile"
    else:
        record = knowledge.records(claim.claim_type).get(claim.record_id)  # type: ignore[arg-type]
        fields = _RECORD_FIELDS[claim.claim_type]
        kind = claim.claim_type.value.replace("_entry", "")
        if record is None:
            return ProfileCheck(
                V.UNSUPPORTED, f"This {kind} isn't in your profile, so it can't be listed."
            )
    # A project's link may be either of the stored URLs.
    if claim.claim_type == ClaimType.PROJECT_ENTRY:
        url = stated.get("url")
        if url and url not in (record.repository_url, record.project_url):
            return ProfileCheck(
                V.CONTRADICTED, f"Your profile has a different link for {record.title}."
            )
    conflicts = [
        f"{_LABELS.get(name, name.replace('_', ' '))} is {_shown(getattr(record, name))}, "
        f"not {_shown(stated.get(name))}"
        for name in fields
        if name in stated and not _same(stated.get(name), getattr(record, name))
    ]
    if conflicts:
        return ProfileCheck(
            V.CONTRADICTED, f"Your {kind} record says: " + "; ".join(conflicts) + "."
        )
    return ProfileCheck(V.SUPPORTED, f"Matches your {kind} record.")


# --- Contradictions with stored information ---------------------------------------------

_YEARS_OF_EXPERIENCE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b(?:\s+of)?(?:\s+[\w-]+){0,4}?\s+experience\b"
    r"|\bexperience\s+of\s+(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_DEGREE_PATTERNS: tuple[tuple[DegreeLevel, re.Pattern[str]], ...] = (
    (DegreeLevel.DOCTORATE, re.compile(r"\b(ph\.?\s?d|doctorate|doctoral)(?![a-z])", re.I)),
    (DegreeLevel.MASTER, re.compile(
        r"\b(master'?s|masters|master of|m\.s\.|m\.sc|msc|m\.tech|mtech|m\.e\.|mba|"
        r"m\.eng|meng)(?![a-z])", re.I)),
    (DegreeLevel.BACHELOR, re.compile(
        r"\b(bachelor'?s|bachelors|bachelor of|b\.s\.|b\.sc|bsc|b\.tech|btech|b\.e\.|"
        r"b\.eng|beng|b\.a\.)(?![a-z])", re.I)),
)  # fmt: skip
_GPA = re.compile(r"\b(?:c?gpa|grade point average)\b\D{0,12}?(\d+(?:\.\d+)?)", re.IGNORECASE)
_SENIORITY = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|head|chief|manager|director|vp|architect)\b", re.I
)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def _degree_levels(text: str) -> set[DegreeLevel]:
    return {level for level, pattern in _DEGREE_PATTERNS if pattern.search(text)}


def check_contradictions(claim: ClaimInput, knowledge: CandidateKnowledge) -> ProfileCheck | None:
    """A claim that conflicts with the stored profile, or None."""
    text = claim.text
    profile = knowledge.profile

    # More years of experience than the work history holds.
    for match in _YEARS_OF_EXPERIENCE.finditer(text):
        claimed = float(match.group(1) or match.group(2))
        total = knowledge.total_experience_years()
        if total is not None and claimed > total + 0.5:
            return ProfileCheck(
                V.CONTRADICTED,
                f"Claims {match.group(0).strip()}, but your work history adds up to about "
                f"{total:.1f} years.",
            )

    # A degree the education records don't show.
    claimed_levels = _degree_levels(text)
    if claimed_levels and profile.educations:
        held = {e.degree_level for e in profile.educations if e.degree_level}
        held |= {lvl for e in profile.educations if e.degree for lvl in _degree_levels(e.degree)}
        if held and not claimed_levels & held:
            recorded = ", ".join(e.degree or e.institution for e in profile.educations)
            return ProfileCheck(
                V.CONTRADICTED, f"Your education records show {recorded}, not this degree."
            )

    # A GPA different from the recorded one.
    gpas = [e.gpa for e in profile.educations if e.gpa is not None]
    for match in _GPA.finditer(text):
        if gpas and all(abs(Decimal(match.group(1)) - g) > Decimal("0.005") for g in gpas):
            recorded = ", ".join(format(g.normalize(), "f") for g in gpas)
            return ProfileCheck(
                V.CONTRADICTED, f"Your recorded GPA is {recorded}, not {match.group(1)}."
            )

    # A more senior role at an employer than the one recorded.
    lower = text.lower()
    for job in profile.work_experiences:
        if job.company_name.lower() not in lower:
            continue
        same_employer = (j for j in profile.work_experiences if j.company_name == job.company_name)
        titles = " ".join(j.title for j in same_employer).lower()
        roles = {m.lower().rstrip(".") for m in _SENIORITY.findall(text)}
        overstated = sorted(w for w in roles if w not in titles)
        if overstated:
            return ProfileCheck(
                V.CONTRADICTED,
                f"Your role at {job.company_name} is recorded as {job.title}, not "
                f"{', '.join(overstated)}.",
            )

    # A year outside the dates of the item the claim belongs to.
    record = knowledge.records(claim.claim_type).get(claim.record_id) if claim.record_id else None
    start = getattr(record, "start_date", None)
    if record is not None and start is not None:
        end = record.end_date
        last = end.year if end else knowledge.today.year if record.is_current else None
        for year in map(int, _YEAR.findall(text)):
            if year < start.year or (last is not None and year > last):
                span = (
                    f"{start.year}\u2013{last}" if last and last != start.year else str(start.year)
                )
                return ProfileCheck(
                    V.CONTRADICTED, f"This item is dated {span}; the claim mentions {year}."
                )
    return None
