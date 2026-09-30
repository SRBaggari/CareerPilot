"""Grounding check: extracted values must actually appear in the resume text.

Applied to every parser's output. It is the main guard against a language model inventing
candidate information: any text value, highlight, or skill that cannot be found in the
source document is dropped (and the candidate is told how many were dropped).
"""

import re
import unicodedata
from typing import Any

from app.resumes.parsed import ParsedItem, ParsedResume

# Derived fields: normalized by the parser, so compared differently or not at all.
_ENUM_FIELDS = {"degree_level", "employment_type", "category"}
_DATE_FIELDS = {"start_date", "end_date", "issue_date", "expiration_date", "achieved_on"}
_NUMBER_FIELDS = {"gpa", "gpa_scale"}
_BOOL_FIELDS = {"is_current"}
_URL_FIELDS = {"website_url", "linkedin_url", "github_url", "project_url", "repository_url",
               "credential_url", "url"}  # fmt: skip
REQUIRED_FIELDS = {"institution", "company_name", "title", "name", "course_name"}

_TRANSLATE = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
    "\u2022": " ", "\u00b7": " ", "|": " ",
})  # fmt: skip


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().translate(_TRANSLATE)
    return " ".join(text.split())


def _normalize_url(url: str) -> str:
    return re.sub(r"^(https?://)?(www\.)?", "", normalize(url)).rstrip("/")


class Grounder:
    def __init__(self, source_text: str) -> None:
        self.text = normalize(source_text)
        self.compact = self.text.replace(" ", "")  # tolerate PDF spacing differences

    def contains(self, value: str) -> bool:
        needle = normalize(value)
        return bool(needle) and (needle in self.text or needle.replace(" ", "") in self.compact)

    def field_ok(self, key: str, value: Any) -> bool:
        if value is None or key in _ENUM_FIELDS or key in _BOOL_FIELDS:
            return True
        if key in _DATE_FIELDS:
            return str(value)[:4] in self.text  # at least the year must appear
        if key in _NUMBER_FIELDS:
            number = str(value).rstrip("0").rstrip(".") if "." in str(value) else str(value)
            return number in self.text
        if key in _URL_FIELDS:
            return _normalize_url(str(value)) in self.text.replace(" ", "")
        return self.contains(str(value))


def ground(parsed: ParsedResume, source_text: str) -> ParsedResume:
    """Return a copy of ``parsed`` containing only values found in ``source_text``."""
    grounder = Grounder(source_text)
    dropped = 0

    personal = {}
    for key, value in parsed.personal.items():
        if grounder.field_ok(key, value):
            personal[key] = value
        else:
            dropped += 1

    items: list[ParsedItem] = []
    for item in parsed.items:
        data = {}
        for key, value in item.data.items():
            # A percentage ("94.2%") implies a 100-point scale without the text saying "100".
            percent_scale = (
                key == "gpa_scale"
                and str(value).split(".")[0] == "100"
                and grounder.contains(f"{item.data.get('gpa')}%")
            )
            if percent_scale or grounder.field_ok(key, value):
                data[key] = value
            else:
                dropped += 1
        if not any(k in data for k in REQUIRED_FIELDS if k in item.data):
            continue  # its required field was not found (already counted above)
        highlights = [h for h in item.highlights if grounder.contains(h)]
        dropped += len(item.highlights) - len(highlights)
        excerpt = item.source_excerpt if grounder.contains(item.source_excerpt) else ""
        items.append(ParsedItem(item.section, data, highlights, excerpt))

    skills = [s for s in parsed.skills if grounder.contains(s.name)]
    dropped += len(parsed.skills) - len(skills)
    for skill in skills:
        if not grounder.contains(skill.source_excerpt):
            skill.source_excerpt = ""

    warnings = list(parsed.warnings)
    if dropped:
        warnings.append(
            f"{dropped} extracted value(s) were discarded because they could not be found "
            "in the resume text."
        )
    return ParsedResume(personal, items, skills, warnings)
