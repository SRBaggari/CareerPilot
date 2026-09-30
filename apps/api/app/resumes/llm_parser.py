"""LLM-backed resume parser (structured outputs). Its results go through the same
grounding check and user review as the rule-based parser's."""

from typing import Any

from app.ai.provider import LLMJsonResult, LLMProvider
from app.profiles.models import SuggestionSection
from app.resumes.parsed import ParsedItem, ParsedResume, ParsedSkill

SYSTEM_PROMPT = """You extract structured data from a candidate's resume.

The output is shown to the candidate for review before anything is saved, and every value
is checked against the resume text; values that cannot be found there are discarded.

- Copy text exactly as written in the resume. Do not paraphrase, summarize, translate,
  correct, or embellish anything.
- Only include information that is explicitly present. Use null (or an empty list) when a
  value is missing; never infer or guess names, dates, grades, employers, or skills.
- "highlights" are the individual bullet points or claims under an entry, each copied
  verbatim as one list item.
- "source_excerpt" is the verbatim resume text the entry came from (heading line plus its
  bullets).
- Dates: "YYYY-MM" when month and year are given, "YYYY" when only the year is given, null
  otherwise. Use is_current=true (and end=null) for "Present"/"Current".
- gpa and gpa_scale as written (e.g. "8.7" and "10"); gpa_scale null if not written."""

_S: dict[str, Any] = {"type": ["string", "null"]}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _entries(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "array",
        "items": _obj(
            {
                **fields,
                "highlights": {"type": "array", "items": {"type": "string"}},
                "source_excerpt": {"type": "string"},
            }
        ),
    }


RESUME_SCHEMA = _obj({
    "full_name": _S, "email": _S, "phone": _S, "location": _S,
    "linkedin_url": _S, "github_url": _S, "website_url": _S, "summary": _S,
    "education": _entries({
        "institution": {"type": "string"}, "degree": _S, "degree_level": {
            "type": ["string", "null"],
            "enum": ["high_school", "certificate", "diploma", "associate", "bachelor", "master",
                     "doctorate", "other", None]},
        "field_of_study": _S, "location": _S, "start": _S, "end": _S, "gpa": _S,
        "gpa_scale": _S,
    }),
    "work_experience": _entries({
        "title": {"type": "string"}, "company": {"type": "string"}, "location": _S,
        "employment_type": {
            "type": ["string", "null"],
            "enum": ["full_time", "part_time", "internship", "contract", "freelance",
                     "volunteer", "other", None]},
        "start": _S, "end": _S, "is_current": {"type": "boolean"},
    }),
    "projects": _entries({
        "title": {"type": "string"}, "role": _S, "description": _S, "project_url": _S,
        "repository_url": _S, "start": _S, "end": _S,
    }),
    "certifications": _entries({
        "name": {"type": "string"}, "issuer": _S, "issue_date": _S, "expiration_date": _S,
        "credential_id": _S, "credential_url": _S,
    }),
    "achievements": _entries({
        "title": {"type": "string"}, "issuer": _S, "date": _S, "description": _S,
    }),
    "coursework": _entries({
        "course_name": {"type": "string"}, "course_code": _S, "term": _S, "grade": _S,
    }),
    "skills": {"type": "array", "items": _obj({
        "name": {"type": "string"},
        "category": {"type": ["string", "null"], "enum": [
            "programming_language", "framework", "library", "tool", "platform", "database",
            "cloud", "methodology", "domain", "soft_skill", "language", "other", None]},
        "source_excerpt": {"type": "string"},
    })},
})  # fmt: skip


def to_iso(value: str | None, *, is_end: bool = False) -> str | None:
    """'2023-05' -> '2023-05-01'; '2023' -> Jan 1 (Dec 1 for range ends)."""
    if not value:
        return None
    parts = value.strip().split("-")
    if not parts[0].isdigit() or len(parts[0]) != 4:
        return None
    month = parts[1] if len(parts) > 1 and parts[1].isdigit() else ("12" if is_end else "01")
    return f"{parts[0]}-{int(month):02d}-01"


def _item(section: SuggestionSection, raw: dict[str, Any], data: dict[str, Any]) -> ParsedItem:
    cleaned = {k: v for k, v in data.items() if v not in (None, "", False)}
    highlights = [h for h in raw.get("highlights") or [] if isinstance(h, str) and h.strip()]
    return ParsedItem(section, cleaned, highlights, str(raw.get("source_excerpt") or ""))


def to_parsed_resume(data: dict[str, Any]) -> ParsedResume:
    personal = {
        "full_name": data.get("full_name"), "contact_email": data.get("email"),
        "phone": data.get("phone"), "location": data.get("location"),
        "linkedin_url": data.get("linkedin_url"), "github_url": data.get("github_url"),
        "website_url": data.get("website_url"), "summary": data.get("summary"),
    }  # fmt: skip
    result = ParsedResume(personal={k: v for k, v in personal.items() if v})
    for e in data.get("education") or []:
        result.items.append(_item(SuggestionSection.EDUCATION, e, {
            "institution": e.get("institution"), "degree": e.get("degree"),
            "degree_level": e.get("degree_level"), "field_of_study": e.get("field_of_study"),
            "location": e.get("location"), "start_date": to_iso(e.get("start")),
            "end_date": to_iso(e.get("end"), is_end=True),
            "gpa": e.get("gpa") if e.get("gpa_scale") else None, "gpa_scale": e.get("gpa_scale"),
        }))  # fmt: skip
    for e in data.get("work_experience") or []:
        result.items.append(_item(SuggestionSection.WORK_EXPERIENCE, e, {
            "title": e.get("title"), "company_name": e.get("company"),
            "location": e.get("location"), "employment_type": e.get("employment_type"),
            "start_date": to_iso(e.get("start")),
            "end_date": None if e.get("is_current") else to_iso(e.get("end"), is_end=True),
            "is_current": bool(e.get("is_current")),
        }))  # fmt: skip
    for e in data.get("projects") or []:
        result.items.append(_item(SuggestionSection.PROJECT, e, {
            "title": e.get("title"), "role": e.get("role"), "description": e.get("description"),
            "project_url": e.get("project_url"), "repository_url": e.get("repository_url"),
            "start_date": to_iso(e.get("start")), "end_date": to_iso(e.get("end"), is_end=True),
        }))  # fmt: skip
    for e in data.get("certifications") or []:
        result.items.append(_item(SuggestionSection.CERTIFICATION, e, {
            "name": e.get("name"), "issuer": e.get("issuer"),
            "issue_date": to_iso(e.get("issue_date")),
            "expiration_date": to_iso(e.get("expiration_date"), is_end=True),
            "credential_id": e.get("credential_id"), "credential_url": e.get("credential_url"),
        }))  # fmt: skip
    for e in data.get("achievements") or []:
        result.items.append(_item(SuggestionSection.ACHIEVEMENT, e, {
            "title": e.get("title"), "issuer": e.get("issuer"),
            "achieved_on": to_iso(e.get("date")), "description": e.get("description"),
        }))  # fmt: skip
    for e in data.get("coursework") or []:
        result.items.append(_item(SuggestionSection.COURSEWORK, e, {
            "course_name": e.get("course_name"), "course_code": e.get("course_code"),
            "term": e.get("term"), "grade": e.get("grade"),
        }))  # fmt: skip
    for s in data.get("skills") or []:
        if isinstance(s, dict) and s.get("name"):
            result.skills.append(ParsedSkill(
                name=str(s["name"]), category=s.get("category"),
                source_excerpt=str(s.get("source_excerpt") or ""),
            ))  # fmt: skip
    return result


class LLMResumeParser:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.name = f"llm:{provider.model}"[:50]

    async def parse(self, text: str) -> tuple[ParsedResume, LLMJsonResult]:
        result = await self.provider.complete_json(
            system=SYSTEM_PROMPT,
            prompt=f"<resume>\n{text}\n</resume>\n\nExtract the resume into the schema.",
            schema=RESUME_SCHEMA,
        )
        return to_parsed_resume(result.data), result
