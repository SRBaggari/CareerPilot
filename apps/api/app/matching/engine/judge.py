"""Rule-based requirement judge.

Semantic retrieval supplies the candidate evidence for each requirement. The judge then
combines three signals, none of which is plain keyword matching on its own:

- **Concepts:** named technologies are normalized ("Retrieval-Augmented Generation" and
  "RAG" are one concept), so wording differences don't matter but the concept must
  actually be present in the cited evidence.
- **Semantic similarity** of the retrieved evidence (provider-calibrated confidence).
- **Structured checks** on profile facts: years of experience, degree level, CGPA,
  graduation year. Eligibility that a profile can't show (work authorization, visas)
  is UNKNOWN, never assumed.

Evidence can only be cited if retrieval returned it; nothing is invented.
"""

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.jobs.analysis.vocabulary import find_technologies
from app.jobs.models import JobRequirement, RequirementType
from app.matching.engine.facts import DEGREE_RANK, CandidateFacts
from app.matching.models import MatchStatus
from app.profiles.models import DegreeLevel, EvidenceSourceType
from app.retrieval.schemas import RetrievedEvidence

M, P, X, U = MatchStatus.MATCHED, MatchStatus.PARTIAL, MatchStatus.MISSING, MatchStatus.UNKNOWN
STATUS_RANK = {X: 0, P: 1, M: 2}


@dataclass
class Assessment:
    requirement_id: uuid.UUID
    status: MatchStatus
    explanation: str
    evidence_ids: list[uuid.UUID]
    semantic_similarity: float | None
    judge: str
    # What the status rests on: "evidence" (cited rows), "profile" (a profile fact without
    # evidence, e.g. a listed skill), or "none".
    basis: str = "evidence"
    # A structured check the status may not exceed (e.g. too few years), and whether the
    # check fully decides the status (e.g. degree level).
    cap: MatchStatus | None = None
    decisive: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def evidence_text(e: RetrievedEvidence) -> str:
    return f"{e.source.record_label or ''}\n{e.factual_content}"


def quote(e: RetrievedEvidence) -> str:
    text = e.factual_content if len(e.factual_content) <= 140 else e.factual_content[:137] + "..."
    where = f" ({e.source.record_label})" if e.source.record_label else ""
    return f"\u201c{text}\u201d{where}"


# --- Evidence helpers ---------------------------------------------------------------------

SOFT_SKILL = re.compile(
    r"communicat|teamwork|team player|collaborat|leadership|problem[- ]solving|attention to "
    r"detail|self[- ]motivated|self[- ]starter|passion|curios|interpersonal|ownership|adaptab"
    r"|work ethic|time management|presentation skills|stakeholder",
    re.IGNORECASE,
)
_NON_APPLIED = (EvidenceSourceType.COURSEWORK, EvidenceSourceType.EDUCATION)
_CREDENTIAL_NOISE = frozenset(
    [
        "certification",
        "certificate",
        "certified",
        "certifications",
        "is",
        "a",
        "an",
        "the",
        "plus",
        "or",
        "and",
        "in",
        "of",
        "preferred",
        "required",
        "nice",
        "to",
        "have",
        "with",
    ]
)


def names(e: RetrievedEvidence, concept: str) -> bool:
    """Whether the evidence (or the item it belongs to) names ``concept``."""
    return concept in find_technologies(evidence_text(e))


def is_applied(e: RetrievedEvidence) -> bool:
    """Project, work, achievement, or profile evidence (not only a course or a degree)."""
    return e.evidence_type not in _NON_APPLIED


def credential_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#]+", text.lower())
    return {w for w in words if w not in _CREDENTIAL_NOISE}


def same_credential(held: set[str], wanted: set[str]) -> bool:
    """The held certification's distinctive words are all in the requirement (or vice versa)."""
    return bool(held) and bool(wanted) and (held <= wanted or wanted <= held)


# --- Structured checks ----------------------------------------------------------------

# Words match case-insensitively; abbreviations case-sensitively ("MS" yes, "ms" no).
_DEGREE_PATTERNS: tuple[tuple[DegreeLevel, re.Pattern[str]], ...] = (
    (DegreeLevel.DOCTORATE, re.compile(r"(?i:\bdoctor(?:ate|al)\b)|\bPh\.?\s?D\b")),
    (
        DegreeLevel.MASTER,
        re.compile(r"(?i:\bmaster'?s?\b)|\bM\.?\s?Tech\b|\bM\.?S\.?c?\b|\bMBA\b|\bMCA\b"),
    ),
    (
        DegreeLevel.BACHELOR,
        re.compile(
            r"(?i:\bbachelor'?s?\b|\bundergraduate degree\b)|\bB\.?\s?Tech\b|\bB\.?E\b"
            r"|\bB\.?S\.?c?\b|\bBCA\b"
        ),
    ),
    (DegreeLevel.DIPLOMA, re.compile(r"(?i:\bdiploma\b)")),
    (DegreeLevel.HIGH_SCHOOL, re.compile(r"(?i:\bhigh school\b|\bsecondary school\b)")),
)
_UNASSESSABLE = re.compile(
    r"authori[sz]ed to work|work authori[sz]ation|visa|sponsor|citizen|permanent resident"
    r"|security clearance|background check|relocat|notice period|willing to|right to work"
    r"|eligible to work|must be based in|minimum age",
    re.IGNORECASE,
)
_GRADUATION = re.compile(
    r"graduat\w*\s+(?:in|by|year|batch)?\s*(?:of\s+)?(?P<y1>20\d\d)|(?P<y2>20\d\d)\s+(?:batch|graduates?|pass\s?outs?)",
    re.IGNORECASE,
)
_GPA = re.compile(
    r"\b(?:c?gpa|cpi)\b[^\d]{0,25}(?P<value>\d{1,2}(?:\.\d{1,2})?)(?:\s*/\s*(?P<scale>\d{1,3}))?",
    re.IGNORECASE,
)


def required_degree(text: str) -> DegreeLevel | None:
    """The lowest degree level the requirement accepts ("Bachelor's or Master's" -> bachelor)."""
    found = [level for level, pattern in _DEGREE_PATTERNS if pattern.search(text)]
    return min(found, key=lambda level: DEGREE_RANK[level]) if found else None


def _education_evidence(facts: CandidateFacts, education_id: uuid.UUID) -> list[uuid.UUID]:
    return facts.evidence_by_subject.get(education_id, [])


def check_education(requirement: JobRequirement, facts: CandidateFacts) -> Assessment | None:
    level = required_degree(requirement.description)
    if level is None:
        return None  # e.g. "a degree in a quantitative field": assessed semantically
    details = {"required_level": level.value}
    if not facts.educations:
        return Assessment(
            requirement.id,
            X,
            "Your profile lists no education.",
            [],
            None,
            "rules",
            basis="none",
            decisive=True,
            details=details,
        )
    ranked = [
        (DEGREE_RANK[e.degree_level], e.degree_level, e)
        for e in facts.educations
        if e.degree_level is not None
    ]
    if not ranked:
        return Assessment(
            requirement.id,
            U,
            "Your education entries don't record a degree level, so this can't be checked.",
            [],
            None,
            "rules",
            basis="none",
            decisive=True,
            details=details,
        )
    rank, best_level, best = max(ranked, key=lambda item: item[0])
    details["candidate_level"] = best_level.value
    if rank < DEGREE_RANK[level]:
        return Assessment(
            requirement.id,
            X,
            f"The role asks for at least a {level.value} degree; your highest listed is "
            f"{best_level.value} ({best.label}).",
            [],
            None,
            "rules",
            basis="profile",
            decisive=True,
            details=details,
        )
    cited = _education_evidence(facts, best.id)
    if cited:
        return Assessment(
            requirement.id,
            M,
            f"Your {best.label} meets the {level.value}-level requirement.",
            cited,
            None,
            "rules",
            decisive=True,
            details=details,
        )
    return Assessment(
        requirement.id,
        P,
        f"Your profile lists {best.label}, which meets the level asked for, but there is no "
        "verified evidence entry for it yet. Add a highlight to that education entry.",
        [],
        None,
        "rules",
        basis="profile",
        decisive=True,
        details=details,
    )


def check_eligibility(requirement: JobRequirement, facts: CandidateFacts) -> Assessment | None:
    text = requirement.description
    if m := _GRADUATION.search(text):
        year = int(m.group("y1") or m.group("y2"))
        ends = [e for e in facts.educations if e.end_date is not None]
        if not ends:
            return Assessment(
                requirement.id,
                U,
                "Your education entries have no end dates, so "
                "the graduation year can't be checked.",
                [],
                None,
                "rules",
                basis="none",
                decisive=True,
                details={"required_year": year},
            )
        match = next((e for e in ends if e.end_date and e.end_date.year == year), None)
        if match:
            cited = _education_evidence(facts, match.id)
            return Assessment(
                requirement.id,
                M if cited else P,
                f"Your {match.label} ends in {year}."
                + ("" if cited else " Add a highlight to that education entry to cite it."),
                cited,
                None,
                "rules",
                basis="evidence" if cited else "profile",
                decisive=True,
                details={"required_year": year},
            )
        years = sorted({e.end_date.year for e in ends if e.end_date})
        return Assessment(
            requirement.id,
            X,
            f"The role is for {year} graduates; your education ends in "
            f"{', '.join(map(str, years))}.",
            [],
            None,
            "rules",
            basis="profile",
            decisive=True,
            details={"required_year": year, "candidate_years": years},
        )
    if m := _GPA.search(text):
        return _check_gpa(requirement, facts, Decimal(m.group("value")), m.group("scale"))
    if _UNASSESSABLE.search(text):
        return Assessment(
            requirement.id,
            U,
            "CareerPilot can't verify this from your profile. Check it yourself before applying.",
            [],
            None,
            "rules",
            basis="none",
            decisive=True,
        )
    return None


def _check_gpa(
    requirement: JobRequirement, facts: CandidateFacts, minimum: Decimal, scale_text: str | None
) -> Assessment:
    details: dict[str, Any] = {"required_gpa": str(minimum)}
    for education in facts.educations:
        if education.gpa is None or education.gpa_scale is None:
            continue
        scale = Decimal(scale_text) if scale_text else None
        # Compare only on the same scale; never convert between scales.
        comparable = education.gpa_scale == scale if scale else minimum <= education.gpa_scale
        if not comparable:
            continue
        details["candidate_gpa"] = f"{education.gpa}/{education.gpa_scale}"
        cited = _education_evidence(facts, education.id)
        if education.gpa >= minimum:
            return Assessment(
                requirement.id,
                M if cited else P,
                f"Your GPA of {education.gpa}/{education.gpa_scale} ({education.label}) meets "
                f"the minimum of {minimum}."
                + ("" if cited else " Add a highlight to that education entry to cite it."),
                cited,
                None,
                "rules",
                basis="evidence" if cited else "profile",
                decisive=True,
                details=details,
            )
        return Assessment(
            requirement.id,
            X,
            f"The minimum is {minimum}; your GPA is {education.gpa}/{education.gpa_scale}.",
            [],
            None,
            "rules",
            basis="profile",
            decisive=True,
            details=details,
        )
    return Assessment(
        requirement.id,
        U,
        "Your profile has no GPA on a comparable scale, so this can't be checked.",
        [],
        None,
        "rules",
        basis="none",
        decisive=True,
        details=details,
    )


# --- The judge ------------------------------------------------------------------------


class RuleJudge:
    name = "rules"

    def __init__(self, thresholds: tuple[float, float]) -> None:
        self.high, self.medium = thresholds

    def assess(
        self, requirement: JobRequirement, retrieved: list[RetrievedEvidence], facts: CandidateFacts
    ) -> Assessment:
        kind = requirement.requirement_type
        structured = None
        if kind == RequirementType.EDUCATION:
            structured = check_education(requirement, facts)
        elif kind == RequirementType.ELIGIBILITY:
            structured = check_eligibility(requirement, facts)
        if structured is not None:
            structured.semantic_similarity = retrieved[0].similarity if retrieved else None
            return structured

        if kind == RequirementType.TECHNOLOGY:
            assessment = self._technology(requirement, retrieved, facts)
        elif kind == RequirementType.CERTIFICATION:
            assessment = self._certification(requirement, retrieved, facts)
        else:
            assessment = self._statement(requirement, retrieved)
        if kind == RequirementType.EXPERIENCE and requirement.min_years is not None:
            self._apply_years(assessment, float(requirement.min_years), facts)
        return assessment

    def _assessment(
        self,
        requirement: JobRequirement,
        status: MatchStatus,
        explanation: str,
        cited: list[RetrievedEvidence],
        top: float | None,
        basis: str = "evidence",
    ) -> Assessment:
        similarity = cited[0].similarity if cited else top
        return Assessment(
            requirement.id,
            status,
            explanation,
            [e.evidence_id for e in cited],
            similarity,
            self.name,
            basis=basis if cited or basis != "evidence" else "none",
        )

    # -- technologies

    def _technology(
        self, requirement: JobRequirement, retrieved: list[RetrievedEvidence], facts: CandidateFacts
    ) -> Assessment:
        name = requirement.description
        concept = (find_technologies(name) or [name])[0]
        top = retrieved[0].similarity if retrieved else None
        naming = [e for e in retrieved if names(e, concept)]
        applied = [e for e in naming if is_applied(e)]
        if applied:
            return self._assessment(
                requirement, M, f"Your evidence shows {name}: {quote(applied[0])}.", applied, top
            )
        if naming:
            return self._assessment(
                requirement,
                P,
                f"{name} appears only in your coursework or education ({quote(naming[0])}); no "
                "project or work evidence shows you using it.",
                naming,
                top,
            )
        if requirement.skill_id in facts.skill_ids or name.lower() in facts.skill_names:
            return self._assessment(
                requirement,
                P,
                f"{name} is in your skills list, but no verified evidence shows you using it. "
                f"Add a highlight that describes how you used {name}.",
                [],
                top,
                basis="profile",
            )
        related = [e for e in retrieved if e.similarity >= self.medium]
        if related:
            return self._assessment(
                requirement,
                P,
                f"Related evidence {quote(related[0])} doesn't mention {name} itself.",
                related[:1],
                top,
            )
        return self._assessment(requirement, X, f"No verified evidence mentions {name}.", [], top)

    # -- certifications: a specific credential, never matched by overlapping words

    def _certification(
        self, requirement: JobRequirement, retrieved: list[RetrievedEvidence], facts: CandidateFacts
    ) -> Assessment:
        wanted = credential_tokens(requirement.description)
        top = retrieved[0].similarity if retrieved else None
        for cert_id, cert_name in facts.certifications.items():
            if same_credential(credential_tokens(cert_name), wanted):
                cited = [e for e in retrieved if e.source.record_id == cert_id]
                cited_ids = facts.evidence_by_subject.get(cert_id, [])
                if cited or cited_ids:
                    assessment = self._assessment(
                        requirement, M, f"You hold \u201c{cert_name}\u201d.", cited, top
                    )
                    assessment.evidence_ids = [e.evidence_id for e in cited] or list(cited_ids)
                    return assessment
                return self._assessment(
                    requirement,
                    P,
                    f"Your profile lists \u201c{cert_name}\u201d; add a highlight to cite it.",
                    [],
                    top,
                    basis="profile",
                )
        related = [e for e in retrieved if e.source.record_type == "certification"]
        if related:
            return self._assessment(
                requirement,
                P,
                f"You hold a related certification ({quote(related[0])}), not this one.",
                related[:1],
                top,
            )
        return self._assessment(
            requirement, X, "No verified evidence of this certification.", [], top
        )

    # -- statements (skills, experience, languages, other)

    def _statement(
        self, requirement: JobRequirement, retrieved: list[RetrievedEvidence]
    ) -> Assessment:
        text = requirement.description
        top = retrieved[0].similarity if retrieved else None
        concepts = find_technologies(text)
        if concepts:
            covering = {c: [e for e in retrieved if names(e, c)] for c in concepts}
            covered = [c for c, found in covering.items() if found]
            cited = list({e.evidence_id: e for c in covered for e in covering[c]}.values())
            applied = [e for e in cited if is_applied(e)]
            if covered and len(covered) == len(concepts) and applied:
                return self._assessment(
                    requirement,
                    M,
                    f"Your evidence covers {', '.join(covered)}: {quote(applied[0])}.",
                    cited,
                    top,
                )
            if covered:
                missing = [c for c in concepts if c not in covered]
                gap = (
                    f"but not {', '.join(missing)}"
                    if missing
                    else "but only in coursework or education, not project or work evidence"
                )
                return self._assessment(
                    requirement,
                    P,
                    f"Your evidence covers {', '.join(covered)} ({quote(cited[0])}) {gap}.",
                    cited,
                    top,
                )

        if retrieved and retrieved[0].similarity >= self.high:
            return self._assessment(
                requirement,
                M,
                f"Closely related evidence: {quote(retrieved[0])}.",
                retrieved[:1],
                top,
            )
        if retrieved and retrieved[0].similarity >= self.medium:
            return self._assessment(
                requirement,
                P,
                f"Somewhat related evidence ({quote(retrieved[0])}) may not fully meet this.",
                retrieved[:1],
                top,
            )
        if requirement.requirement_type == RequirementType.LANGUAGE or SOFT_SKILL.search(text):
            return self._assessment(
                requirement,
                U,
                "No verified evidence addresses this. Qualities like this are hard to show in a "
                "profile; consider adding a highlight if it applies to you.",
                [],
                top,
                basis="none",
            )
        return self._assessment(requirement, X, "No verified evidence addresses this.", [], top)

    # -- experience years

    @staticmethod
    def _apply_years(assessment: Assessment, required: float, facts: CandidateFacts) -> None:
        years = facts.experience_years
        assessment.details.update({"required_years": required, "candidate_years": years})
        if years is None:
            assessment.cap = P
            note = " Your work experience has no dates, so the years can't be checked."
        elif years < required:
            assessment.cap = P
            note = (
                f" Your dated work experience totals {years:g} years; "
                f"the role asks for {required:g}+."
            )
        else:
            note = f" Your dated work experience totals {years:g} years ({required:g}+ asked)."
        if assessment.status == M and assessment.cap == P:
            assessment.status = P
        assessment.details["years_note"] = note.strip()
        assessment.explanation += note
