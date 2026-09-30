"""Parser output: structured, unverified candidate information extracted from a resume.

Nothing here is trusted. Every item becomes a pending suggestion that the candidate must
accept before it reaches the master profile.
"""

from dataclasses import dataclass, field
from typing import Any

from app.profiles.models import SuggestionSection

ITEM_SECTIONS = (
    SuggestionSection.EDUCATION,
    SuggestionSection.WORK_EXPERIENCE,
    SuggestionSection.PROJECT,
    SuggestionSection.CERTIFICATION,
    SuggestionSection.ACHIEVEMENT,
    SuggestionSection.COURSEWORK,
)


@dataclass
class ParsedItem:
    section: SuggestionSection
    data: dict[str, Any]  # field names match the section's input schema
    highlights: list[str] = field(default_factory=list)  # one claim each -> evidence
    source_excerpt: str = ""  # verbatim resume text the item came from


@dataclass
class ParsedSkill:
    name: str
    category: str | None
    source_excerpt: str


@dataclass
class ParsedResume:
    personal: dict[str, Any] = field(default_factory=dict)
    items: list[ParsedItem] = field(default_factory=list)
    skills: list[ParsedSkill] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
