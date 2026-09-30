"""Rule-based job description analyzer (offline, deterministic).

Importance comes only from the text: a requirements-type heading or explicit wording
("must", "required", "minimum") makes a statement REQUIRED; "preferred", "nice to have",
"a plus" make it PREFERRED; everything else (duties, company and stack descriptions,
unlabelled prose) is INFORMATIONAL. Nothing is promoted without an explicit cue.
"""

import re
from decimal import Decimal
from enum import StrEnum

from app.jobs.analysis.extracted import (
    IMPORTANCE_RANK,
    ExtractedRequirement,
    JobExtraction,
)
from app.jobs.analysis.fields import employment_type, parse_deadline, parse_salary, work_mode
from app.jobs.analysis.vocabulary import find_technologies
from app.jobs.models import RequirementImportance, RequirementType
from app.profiles.models import EmploymentType

Imp, Req = RequirementImportance, RequirementType


class Role(StrEnum):
    HEADER = "header"
    PROSE = "prose"  # text outside any recognized section: only explicit cues count
    ABOUT = "about"
    RESPONSIBILITIES = "responsibilities"
    REQUIRED = "required"
    PREFERRED = "preferred"
    ELIGIBILITY = "eligibility"
    TECH = "tech"
    BENEFITS = "benefits"
    OTHER = "other"


_HEADINGS: dict[Role, tuple[str, ...]] = {
    Role.REQUIRED: (
        "requirements", "qualifications", "minimum qualifications", "basic qualifications",
        "required qualifications", "required skills", "what you ll need", "what you need",
        "what we re looking for", "what we are looking for", "what you bring", "must have",
        "must haves", "you have", "who you are", "skills required", "job requirements",
        "skills and qualifications", "key requirements", "requirements and qualifications",
        "about you", "you should have", "mandatory skills",
    ),
    Role.PREFERRED: (
        "preferred qualifications", "preferred skills", "nice to have", "nice to haves",
        "good to have", "bonus points", "bonus", "pluses", "desired skills", "desirable",
        "preferred", "additional qualifications", "extra credit", "it would be great if you",
    ),
    Role.RESPONSIBILITIES: (
        "responsibilities", "key responsibilities", "what you ll do", "what you will do",
        "your role", "role and responsibilities", "roles and responsibilities", "duties",
        "job duties", "day to day", "what you ll be doing", "in this role you will",
        "your responsibilities", "the job", "what you ll work on",
    ),
    Role.ELIGIBILITY: (
        "eligibility", "eligibility criteria", "who can apply", "work authorization",
    ),
    Role.TECH: ("tech stack", "our stack", "technologies", "tools we use", "our tech stack"),
    Role.BENEFITS: (
        "benefits", "perks", "compensation", "salary", "what we offer", "why join us",
        "compensation and benefits", "perks and benefits", "stipend",
    ),
    Role.ABOUT: (
        "about us", "about the company", "about the team", "who we are", "company overview",
        "about the role", "job description", "overview", "summary", "the role", "about",
    ),
    Role.OTHER: ("how to apply", "application process", "equal opportunity", "note"),
}  # fmt: skip
_HEADING_LOOKUP = {h: role for role, headings in _HEADINGS.items() for h in headings}
_ABOUT_COMPANY = re.compile(r"^[Aa]bout\s+(?!us\b|the\b|you\b)(?P<company>[A-Z][\w&.\- ]{1,60})$")

_LABELS = {
    "title": ("job title", "title", "position", "role", "designation", "job role"),
    "company": ("company", "organization", "organisation", "employer", "company name"),
    "location": ("location", "job location", "work location", "based in", "office location"),
    "work_mode": ("work mode", "workplace", "workplace type", "work type", "remote"),
    "employment": ("employment type", "job type", "type", "employment", "contract type"),
    # Recognized so these lines are never mistaken for requirements.
    "salary": (
        "salary",
        "stipend",
        "ctc",
        "compensation",
        "pay",
        "pay range",
        "package",
        "salary range",
        "base salary",
    ),
    "deadline": (
        "deadline",
        "application deadline",
        "last date to apply",
        "last date",
        "apply by",
        "closing date",
    ),
}
_LABEL_LOOKUP = {label: key for key, labels in _LABELS.items() for label in labels}

BULLET_RE = re.compile(r"^\s*(?:[\u2022*\-\u2013\u2014>o]|\d{1,2}[.)])\s+")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
PREFERRED_CUE = re.compile(
    r"\b(preferred|preferably|nice[- ]to[- ]have|good[- ]to[- ]have|(?:is |are |a |big |huge )plus"
    r"|bonus|desirable|ideally|advantageous|an advantage|beneficial|optional"
    r"|would be (?:nice|great|a bonus)|nice if)\b",
    re.IGNORECASE,
)
REQUIRED_CUE = re.compile(
    r"\b(must|required|requires?|mandatory|minimum|at least|essential|need to have)\b",
    re.IGNORECASE,
)
YEARS_RE = re.compile(
    r"(?P<years>\d{1,2}(?:\.\d)?)\s*\+?\s*(?:(?:-|\u2013|to)\s*\d{1,2}\s*)?\+?\s*years?", re.I
)
_TYPE_RULES: tuple[tuple[RequirementType, re.Pattern[str]], ...] = (
    (Req.ELIGIBILITY, re.compile(
        r"authori[sz]ed to work|work authori[sz]ation|visa|sponsor|citizen|permanent resident"
        r"|security clearance|background check|relocat|graduat(?:e|ing|ion) (?:in|by|year)"
        r"|\b20\d\d (?:batch|graduates?|pass ?outs?)\b|batch of 20\d\d|backlogs?|minimum age"
        r"|eligible to work|notice period|willing to work|must be based in|right to work",
        re.I)),
    (Req.CERTIFICATION, re.compile(r"\bcertif(?:ied|ication|icate)s?\b", re.I)),
    (Req.EDUCATION, re.compile(
        # Words case-insensitively; abbreviations case-sensitively so "be"/"ms" in ordinary
        # prose ("must be", "ms latency") don't read as degrees.
        r"(?i:\bdegree\b|\bbachelor|\bmaster'?s\b|\bdiploma\b|\bc?gpa\b"
        r"|or (?:a )?related field|or equivalent (?:practical )?experience)"
        r"|\bB\.?\s?E\b|\bB\.?\s?Tech\b|\bM\.?\s?Tech\b|\bB\.?S\.?c?\b|\bM\.?S\.?c?\b"
        r"|\bPh\.?\s?D\b|\bMBA\b|\bBCA\b|\bMCA\b")),
    (Req.EXPERIENCE, re.compile(r"\byears?\b.*\bexperience\b|\bexperience\b.*\byears?\b"
                                r"|\bproven experience\b|\bprofessional experience\b", re.I)),
    (Req.LANGUAGE, re.compile(
        r"\b(?:fluen(?:t|cy)|proficien(?:t|cy)|native|spoken|written)\b.{0,30}\b(?:english|hindi"
        r"|french|german|spanish|mandarin|japanese|telugu|tamil|kannada)\b"
        r"|\b(?:english|hindi|french|german|spanish|mandarin|japanese)\b.{0,15}\b(?:fluency"
        r"|proficiency|speaking|language skills)\b", re.I)),
)  # fmt: skip


TITLE_SEPARATOR = r"\s+(?:at|@)\s+|\s+[|\u2013\u2014-]\s+"
LOCATION_LIKE = re.compile(r"^(?:remote|[A-Z][A-Za-z .]+,\s*[A-Z][A-Za-z .]+)$", re.IGNORECASE)


def _norm_heading(line: str) -> str:
    text = line.strip().rstrip(":").lower().replace("&", " and ").replace("\u2019", "'")
    text = re.sub(r"[^a-z ]", " ", text)
    return " ".join(text.split())


def heading_role(line: str) -> Role | None:
    if len(line) > 60 or BULLET_RE.match(line):
        return None
    if _ABOUT_COMPANY.match(line.strip().rstrip(":")):
        return Role.ABOUT
    return _HEADING_LOOKUP.get(_norm_heading(line))


def _label(line: str) -> tuple[str, str] | None:
    key, sep, value = line.partition(":")
    if sep and value.strip() and len(key) <= 30:
        field = _LABEL_LOOKUP.get(_norm_heading(key))
        if field:
            return field, value.strip()
    return None


def split_sections(text: str) -> list[tuple[Role, list[str]]]:
    sections: list[tuple[Role, list[str]]] = [(Role.HEADER, [])]
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        role = heading_role(line)
        if role is not None:
            sections.append((role, [line] if role == Role.ABOUT and _ABOUT_COMPANY.match(
                line.rstrip(":")) else []))  # fmt: skip
            continue
        sections[-1][1].append(line)
    return sections


def statements(lines: list[str]) -> list[str]:
    """Bullets are statements; prose lines are split into sentences. Wrapped lines join."""
    result: list[str] = []
    for line in lines:
        if BULLET_RE.match(line):
            result.append(BULLET_RE.sub("", line).strip())
        elif result and line[:1].islower():
            result[-1] = f"{result[-1]} {line}"
        else:
            result.extend(s.strip() for s in SENTENCE_SPLIT.split(line) if s.strip())
    return [s for s in result if len(s) >= 3]


def importance_for(statement: str, role: Role) -> RequirementImportance:
    if role in (Role.RESPONSIBILITIES, Role.ABOUT, Role.TECH, Role.BENEFITS):
        return Imp.INFORMATIONAL  # descriptions of the job, never candidate requirements
    if PREFERRED_CUE.search(statement):
        return Imp.PREFERRED
    if role in (Role.REQUIRED, Role.ELIGIBILITY) or REQUIRED_CUE.search(statement):
        return Imp.REQUIRED
    if role == Role.PREFERRED:
        return Imp.PREFERRED
    return Imp.INFORMATIONAL


def type_for(statement: str, role: Role) -> RequirementType:
    if role == Role.RESPONSIBILITIES:
        return Req.RESPONSIBILITY
    if role == Role.ELIGIBILITY:
        return Req.ELIGIBILITY
    for requirement_type, pattern in _TYPE_RULES:
        if pattern.search(statement):
            return requirement_type
    if (
        role in (Role.REQUIRED, Role.PREFERRED)
        or REQUIRED_CUE.search(statement)
        or PREFERRED_CUE.search(statement)
    ):
        return Req.SKILL
    return Req.OTHER


def min_years(statement: str) -> Decimal | None:
    m = YEARS_RE.search(statement)
    return Decimal(m.group("years")) if m else None


class HeuristicJobAnalyzer:
    name = "heuristic"

    def analyze(self, text: str) -> JobExtraction:
        result = JobExtraction()
        sections = split_sections(text)
        header = sections[0][1]
        labels: dict[str, str] = {}
        for _, lines in sections:
            for line in lines:
                if (label := _label(line)) and label[0] not in labels:
                    labels[label[0]] = label[1]

        result.title = labels.get("title") or self._title_from_header(header)
        result.company_name = labels.get("company") or self._company(sections, header)
        result.location = labels.get("location")

        header_text = "\n".join([*header, *labels.values()])
        result.workplace_type, modes = work_mode(labels.get("work_mode") or header_text)
        if result.workplace_type is None and not modes:
            result.workplace_type, modes = work_mode(text)
        if len(modes) > 1 and result.workplace_type is None:
            result.warnings.append("Several work modes are mentioned, so none was set.")
        employment_text = labels.get("employment") or f"{result.title or ''}\n{header_text}"
        result.employment_type, kinds = employment_type(employment_text)
        if result.employment_type is None and not kinds:
            result.employment_type, kinds = employment_type(text)
        if result.employment_type is None and set(kinds) == {
            EmploymentType.INTERNSHIP,
            EmploymentType.FULL_TIME,
        }:
            result.employment_type = None  # e.g. "internship with a full-time offer": ambiguous
        if len(kinds) > 1 and result.employment_type is None:
            result.warnings.append("Several employment types are mentioned, so none was set.")

        result.salary = parse_salary(text)
        result.application_deadline, result.deadline_text, warning = parse_deadline(text)
        if warning:
            result.warnings.append(warning)

        technologies: dict[str, ExtractedRequirement] = {}
        for role, lines in sections:
            if role in (Role.BENEFITS, Role.OTHER):
                continue
            if role == Role.HEADER:  # apart from the title line, header text is plain prose
                title_line = self._title_line(header)
                role, lines = Role.PROSE, [ln for ln in lines if ln != title_line]
            for statement in statements([ln for ln in lines if not _label(ln)]):
                importance = importance_for(statement, role)
                requirement_type = type_for(statement, role)
                if role == Role.TECH:
                    requirement_type = Req.OTHER
                if (
                    requirement_type != Req.OTHER
                    or importance != Imp.INFORMATIONAL
                    or role == Role.TECH
                ):
                    result.requirements.append(ExtractedRequirement(
                        requirement_type, importance, statement[:2000], statement[:2000],
                        min_years(statement) if requirement_type == Req.EXPERIENCE else None,
                    ))  # fmt: skip
                for tech in find_technologies(statement):
                    current = technologies.get(tech)
                    if (
                        current is None
                        or IMPORTANCE_RANK[importance] > IMPORTANCE_RANK[current.importance]
                    ):
                        technologies[tech] = ExtractedRequirement(
                            Req.TECHNOLOGY, importance, tech, statement[:2000]
                        )
        result.requirements.extend(technologies.values())
        return result

    @staticmethod
    def _title_line(header: list[str]) -> str | None:
        """The first short, unlabelled header line (e.g. "Backend Developer - Lumen Health")."""
        for line in header[:3]:
            if not _label(line) and len(line) <= 90 and not line.endswith("."):
                return line
        return None

    @classmethod
    def _title_from_header(cls, header: list[str]) -> str | None:
        line = cls._title_line(header)
        title = re.split(TITLE_SEPARATOR, line)[0].strip() if line else ""
        return title if 2 <= len(title) <= 90 else None

    @classmethod
    def _company(cls, sections: list[tuple[Role, list[str]]], header: list[str]) -> str | None:
        if line := cls._title_line(header):  # "Title at Company" / "Title - Company"
            parts = [p.strip(" ,(") for p in re.split(TITLE_SEPARATOR, line)]
            if len(parts) >= 2 and parts[1] and not LOCATION_LIKE.match(parts[1]):
                return parts[1]
        for role, lines in sections:
            if role == Role.ABOUT and lines and (m := _ABOUT_COMPANY.match(lines[0].rstrip(":"))):
                return m.group("company").strip()
        return None
