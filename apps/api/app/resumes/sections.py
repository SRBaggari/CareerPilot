"""Split resume text into sections by recognizing common headings."""

import re
from enum import StrEnum


class SectionKind(StrEnum):
    HEADER = "header"  # everything before the first heading (name, contact details)
    SUMMARY = "summary"
    EDUCATION = "education"
    EXPERIENCE = "experience"
    PROJECTS = "projects"
    SKILLS = "skills"
    CERTIFICATIONS = "certifications"
    ACHIEVEMENTS = "achievements"
    COURSEWORK = "coursework"
    OTHER = "other"  # recognized but not imported (languages, interests, references, ...)


_HEADINGS: dict[SectionKind, tuple[str, ...]] = {
    SectionKind.SUMMARY: (
        "summary", "professional summary", "profile", "professional profile", "objective",
        "career objective", "about me", "about",
    ),
    SectionKind.EDUCATION: (
        "education", "academic background", "academics", "education and training",
        "educational qualifications", "academic qualifications", "educational background",
    ),
    SectionKind.EXPERIENCE: (
        "experience", "work experience", "professional experience", "employment",
        "employment history", "work history", "internships", "internship",
        "internship experience", "relevant experience", "industry experience",
    ),
    SectionKind.PROJECTS: (
        "projects", "personal projects", "academic projects", "key projects",
        "selected projects", "project experience", "technical projects",
    ),
    SectionKind.SKILLS: (
        "skills", "technical skills", "core competencies", "technologies", "key skills",
        "tools and technologies", "skills and tools", "technical proficiencies", "skill set",
    ),
    SectionKind.CERTIFICATIONS: (
        "certifications", "certificates", "certification", "licenses and certifications",
        "certifications and licenses", "courses and certifications",
    ),
    SectionKind.ACHIEVEMENTS: (
        "achievements", "awards", "honors", "honours", "honors and awards", "awards and honors",
        "awards and achievements", "achievements and awards", "accomplishments",
    ),
    SectionKind.COURSEWORK: ("coursework", "relevant coursework", "courses", "key courses"),
    SectionKind.OTHER: (
        "languages", "interests", "hobbies", "volunteering", "volunteer experience",
        "publications", "references", "extracurricular activities", "activities",
        "leadership", "positions of responsibility", "declaration",
    ),
}  # fmt: skip
_LOOKUP = {heading: kind for kind, headings in _HEADINGS.items() for heading in headings}
MAX_HEADING_CHARS = 45


def _normalize_heading(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z ]", " ", text)
    return " ".join(text.split())


def match_heading(line: str) -> tuple[SectionKind, str] | None:
    """Return (section, trailing content) if ``line`` is a heading, e.g. "SKILLS" or
    "Relevant Coursework: Algorithms, Databases"."""
    head, sep, rest = line.partition(":")
    if len(head) <= MAX_HEADING_CHARS:
        kind = _LOOKUP.get(_normalize_heading(head))
        if kind is not None and (not sep or rest.strip() or head == line.rstrip(":")):
            return kind, rest.strip()
    return None


def detect_sections(text: str) -> dict[SectionKind, list[str]]:
    sections: dict[SectionKind, list[str]] = {SectionKind.HEADER: []}
    current = SectionKind.HEADER
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = match_heading(line)
        # Inside Skills, "Languages: Python, Java" is a labelled skill line, not a heading.
        if heading is not None and current == SectionKind.SKILLS and heading[1]:
            heading = None
        if heading is not None:
            current, rest = heading
            sections.setdefault(current, [])
            if rest:
                sections[current].append(rest)
            continue
        sections[current].append(line)
    return sections
