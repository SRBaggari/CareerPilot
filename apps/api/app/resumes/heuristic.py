"""Rule-based resume parser. Works offline and deterministically.

Heuristics are necessarily imperfect; that is acceptable because every result is only a
suggestion the candidate reviews. The parser never fills a field with a guess: if a
required value can't be found, the entry is skipped and a warning is recorded.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.profiles.models import SuggestionSection
from app.resumes.parsed import ParsedItem, ParsedResume, ParsedSkill
from app.resumes.sections import SectionKind, detect_sections

# --- Patterns -----------------------------------------------------------------------------

BULLET_RE = re.compile(r"^\s*(?:[\u2022*\-\u2013\u2014>o]|\d{1,2}[.)])\s+")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{7,}\d(?![\w/])")
URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|io|dev|org|net|me|ai|co|app|edu)"
    r"(?:/[^\s|,()]*)?",
    re.IGNORECASE,
)
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_YEAR = r"(?:19|20)\d{2}"
_DATE = rf"(?:{_MONTH}\s*,?\s*{_YEAR}|\d{{1,2}}/{_YEAR}|{_YEAR})"
_OPEN_END = r"present|current|now|ongoing|till date|to date"
RANGE_RE = re.compile(
    rf"\(?\b(?P<start>{_DATE})\s*(?:-|\u2013|\u2014|to|until)\s*(?P<end>{_DATE}|{_OPEN_END})\b\)?",
    re.IGNORECASE,
)
SINGLE_DATE_RE = re.compile(rf"\(?\b(?:expected\s+)?(?P<date>{_DATE})\b\)?", re.IGNORECASE)
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}  # fmt: skip
GPA_RE = re.compile(
    r"\b(?:c?gpa|cpi|sgpa)\b\s*[:\-]?\s*(?P<gpa>\d{1,2}(?:\.\d{1,2})?)\s*(?:/\s*(?P<scale>\d{1,3}(?:\.\d{1,2})?))?",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(r"\b(?P<pct>\d{2}(?:\.\d{1,2})?)\s*%")
INSTITUTION_RE = re.compile(
    r"\b(university|college|institute|school|academy|polytechnic|iit|nit|iiit|bits)\b", re.I
)
DEGREE_RE = re.compile(
    r"\b(bachelor|master|doctor|ph\.?\s?d|mba|associate|diploma|high school|secondary|hsc|ssc"
    r"|intermediate|b\.?\s?tech|m\.?\s?tech|b\.?\s?e\b|m\.?\s?e\b|b\.?\s?sc|m\.?\s?sc|b\.?\s?s\b"
    r"|m\.?\s?s\b|b\.?\s?a\b|m\.?\s?a\b|bca|mca|b\.?\s?com|m\.?\s?com|b\.?\s?eng|m\.?\s?eng)",
    re.IGNORECASE,
)
ROLE_RE = re.compile(
    r"\b(engineer|developer|intern|analyst|scientist|manager|assistant|consultant|designer"
    r"|researcher|lead|associate|architect|specialist|administrator|coordinator|trainee|fellow"
    r"|volunteer|tutor|teaching|officer|director|programmer|representative|technician|head)\b",
    re.IGNORECASE,
)
COMPANY_RE = re.compile(
    r"\b(inc|ltd|llc|llp|corp|corporation|company|technologies|solutions|labs|pvt|limited|group"
    r"|systems|software|services|consulting|ventures)\b\.?",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(r"^(?:remote|[A-Z][A-Za-z .]+,\s*[A-Z][A-Za-z .]+)$")
FRAGMENT_SPLIT_RE = re.compile(r"\s+[|\u2022\u00b7]\s+|\s+[\u2013\u2014-]\s+|\s{2,}|\t")
LIST_SPLIT_RE = re.compile(r"\s*[,;|\u2022\u00b7]\s*(?![^()]*\))")

SKILL_CATEGORIES = (
    ("language", "programming_language"), ("framework", "framework"), ("librar", "library"),
    ("tool", "tool"), ("database", "database"), ("cloud", "cloud"), ("platform", "platform"),
    ("soft", "soft_skill"), ("devops", "tool"),
)  # fmt: skip
DEGREE_LEVELS = (
    (r"ph\.?\s?d|doctor", "doctorate"),
    (r"master|m\.?\s?tech|m\.?\s?sc|mba|mca|m\.?\s?s\b|m\.?\s?e\b|m\.?\s?a\b"
     r"|m\.?\s?com|m\.?\s?eng", "master"),
    (r"bachelor|b\.?\s?tech|b\.?\s?sc|bca|b\.?\s?e\b|b\.?\s?s\b|b\.?\s?a\b"
     r"|b\.?\s?com|b\.?\s?eng", "bachelor"),
    (r"associate", "associate"), (r"diploma", "diploma"),
    (r"high school|secondary|hsc|ssc|intermediate", "high_school"),
)  # fmt: skip


# --- Helpers ------------------------------------------------------------------------------


# A year with no month or numeric month next to it, e.g. "2022 - 2026" or "(2024)".
YEAR_ONLY_RE = re.compile(r"(?<![A-Za-z]{3} )(?<![A-Za-z]{4} )(?<![/\d])(?:19|20)\d{2}(?![/\d])")


def parse_date(token: str, *, is_end: bool = False) -> date | None:
    token = token.strip().lower().rstrip(".")
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", token):
        month, year = int(m.group(1)), int(m.group(2))
        return date(year, month, 1) if 1 <= month <= 12 else None
    if m := re.search(rf"({_YEAR})", token):
        year = int(m.group(1))
        month_word = re.match(r"[a-z]{3}", token)
        if month_word and month_word.group(0) in MONTHS:
            return date(year, MONTHS[month_word.group(0)], 1)
        return date(year, 12 if is_end else 1, 1)
    return None


def find_range(text: str) -> tuple[date | None, date | None, bool]:
    """(start, end, is_open_ended) from the first date range in ``text``."""
    if m := RANGE_RE.search(text):
        end_token = m.group("end")
        open_ended = bool(re.fullmatch(_OPEN_END, end_token, re.IGNORECASE))
        end = None if open_ended else parse_date(end_token, is_end=True)
        return parse_date(m.group("start")), end, open_ended
    if m := SINGLE_DATE_RE.search(text):  # a lone date usually marks the end/graduation
        return None, parse_date(m.group("date"), is_end=True), False
    return None, None, False


def strip_dates(text: str) -> str:
    return SINGLE_DATE_RE.sub("", RANGE_RE.sub("", text))


def fragments(lines: list[str]) -> list[str]:
    parts: list[str] = []
    for line in lines:
        for part in FRAGMENT_SPLIT_RE.split(strip_dates(line)):
            part = part.strip(" ,|:;-\u2013\u2014()")
            if part:
                parts.append(part)
    return parts


def strip_bullet(line: str) -> str:
    return BULLET_RE.sub("", line).strip()


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


@dataclass
class Entry:
    header: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)


def split_entries(lines: list[str]) -> list[Entry]:
    """Group lines into entries: heading lines followed by bullet points."""
    entries: list[Entry] = []
    current: Entry | None = None
    for line in lines:
        if BULLET_RE.match(line):
            if current is None:
                current = Entry()
                entries.append(current)
            current.bullets.append(strip_bullet(line))
        elif current is not None and current.bullets and line[:1].islower():
            current.bullets[-1] = f"{current.bullets[-1]} {line}"  # wrapped bullet
        elif (
            current is not None
            and not current.bullets
            and len(current.header) < 3
            and not (RANGE_RE.search(line) and RANGE_RE.search(" ".join(current.header)))
        ):
            current.header.append(line)
        else:
            current = Entry(header=[line])
            entries.append(current)
        current.raw.append(line)
    return entries


def split_list(line: str) -> list[str]:
    return [p.strip(" .") for p in LIST_SPLIT_RE.split(strip_bullet(line)) if p.strip(" .")]


# --- Section parsers ----------------------------------------------------------------------


def _parse_personal(header: list[str], summary: list[str]) -> dict[str, Any]:
    text = "\n".join(header)
    personal: dict[str, Any] = {}
    for line in header:
        candidate = FRAGMENT_SPLIT_RE.split(line)[0].strip()
        words = candidate.split()
        if (
            2 <= len(words) <= 5
            and re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*", candidate)
            and not URL_RE.search(candidate)
        ):
            personal["full_name"] = candidate.title() if candidate.isupper() else candidate
            break
    if m := EMAIL_RE.search(text):
        personal["contact_email"] = m.group(0)
    if m := PHONE_RE.search(EMAIL_RE.sub("", text)):
        personal["phone"] = m.group(0).strip()
    for url in URL_RE.findall(EMAIL_RE.sub("", text)):
        key = (
            "linkedin_url" if "linkedin." in url.lower()
            else "github_url" if "github." in url.lower()
            else "website_url"
        )  # fmt: skip
        personal.setdefault(key, url.rstrip("/."))
    for line in header:
        for part in FRAGMENT_SPLIT_RE.split(line):
            part = part.strip()
            if LOCATION_RE.match(part) and part != personal.get("full_name"):
                personal.setdefault("location", part)
    if summary:
        personal["summary"] = " ".join(strip_bullet(line) for line in summary)[:5000]
    return personal


def _education(entry: Entry) -> dict[str, Any] | None:
    header_text = " ".join(entry.header)
    parts = fragments(entry.header)
    institution = next((p for p in parts if INSTITUTION_RE.search(p)), parts[0] if parts else None)
    degree = next((p for p in parts if p != institution and DEGREE_RE.search(p)), None)
    if degree is None and institution and DEGREE_RE.search(institution) and len(parts) > 1:
        degree, institution = institution, next(p for p in parts if p != institution)
    if not institution:
        return None
    data: dict[str, Any] = {"institution": institution}
    if degree:
        degree = GPA_RE.sub("", degree).strip(" ,")
        name, field_of_study = degree, None
        if m := re.search(r"\s+in\s+(.+)$", degree):
            name, field_of_study = degree[: m.start()], m.group(1)
        elif m := re.search(r"\((.+)\)", degree):
            name, field_of_study = degree[: m.start()].strip(), m.group(1)
        data["degree"] = name.strip()[:200]
        if field_of_study:
            data["field_of_study"] = field_of_study.strip()[:200]
        for pattern, level in DEGREE_LEVELS:
            if re.search(pattern, degree, re.IGNORECASE):
                data["degree_level"] = level
                break
    if location := next((p for p in parts if LOCATION_RE.match(p) and p != institution), None):
        data["location"] = location
    start, end, _ = find_range(header_text)
    data["start_date"], data["end_date"] = _iso(start), _iso(end)
    all_text = " ".join(entry.raw)
    if (m := GPA_RE.search(all_text)) and m.group("scale"):
        data["gpa"], data["gpa_scale"] = m.group("gpa"), m.group("scale")
    elif m := PERCENT_RE.search(all_text):
        data["gpa"], data["gpa_scale"] = m.group("pct"), "100"
    return data


def _experience(entry: Entry) -> dict[str, Any] | None:
    parts: list[str] = []
    for part in fragments(entry.header):
        if " at " in part and ROLE_RE.search(part.split(" at ")[0]):
            parts.extend(p.strip() for p in part.split(" at ", 1))
        elif ", " in part and ROLE_RE.search(part.split(", ")[0]):  # "Title, Employer"
            parts.extend(p.strip() for p in part.split(", ", 1))
        else:
            parts.append(part)
    location = next((p for p in parts if LOCATION_RE.match(p)), None)
    parts = [p for p in parts if p != location]
    title = next((p for p in parts if ROLE_RE.search(p) and not COMPANY_RE.search(p)), None)
    company = next((p for p in parts if p != title and COMPANY_RE.search(p)), None)
    company = company or next((p for p in parts if p != title), None)
    if not title or not company:
        return None
    start, end, current = find_range(" ".join(entry.header))
    lowered = " ".join(entry.header).lower()
    employment_type = next(
        (value for key, value in (
            ("intern", "internship"), ("part-time", "part_time"), ("part time", "part_time"),
            ("contract", "contract"), ("freelance", "freelance"), ("volunteer", "volunteer"),
            ("full-time", "full_time"), ("full time", "full_time"),
        ) if key in lowered),
        None,
    )  # fmt: skip
    return {
        "title": title[:200], "company_name": company[:300], "location": location,
        "employment_type": employment_type, "start_date": _iso(start),
        "end_date": _iso(end), "is_current": current,
    }  # fmt: skip


def _project(entry: Entry) -> dict[str, Any] | None:
    all_text = " ".join(entry.raw)
    urls = [u.rstrip("/.") for u in URL_RE.findall(all_text) if "." in u]
    header = [URL_RE.sub("", line) for line in entry.header]
    # "Title - subtitle | tech, list": the title runs up to the first " | ".
    if header and " | " in header[0]:
        title, rest = header[0].split(" | ", 1)
        title = strip_dates(title).strip(" ,|:;-" + chr(0x2013) + chr(0x2014) + "()")
        parts = [title, *fragments([rest, *header[1:]])] if title else fragments(header)
    else:
        parts = fragments(header)
    if not parts:
        return None
    start, end, _ = find_range(" ".join(entry.header))
    repo = next((u for u in urls if re.search(r"github\.|gitlab\.|bitbucket\.", u, re.I)), None)
    site = next((u for u in urls if u != repo), None)
    description = " | ".join(parts[1:]) or None
    return {
        "title": parts[0][:300], "description": description, "repository_url": repo,
        "project_url": site, "start_date": _iso(start), "end_date": _iso(end),
    }  # fmt: skip


def _single_line_items(lines: list[str]) -> list[str]:
    """One item per line; lowercase-starting lines continue the previous one."""
    items: list[str] = []
    for line in lines:
        text = strip_bullet(line)
        if items and not BULLET_RE.match(line) and text[:1].islower():
            items[-1] = f"{items[-1]} {text}"
        elif text:
            items.append(text)
    return items


def _is_link(candidate: str) -> bool:
    """A real link (scheme, www. or a path), not a dotted name such as "DeepLearning.AI"."""
    return bool(re.match(r"(?i)https?://|www\.", candidate)) or "/" in candidate


def _certification(line: str) -> dict[str, Any] | None:
    _, issued, _ = find_range(line)
    urls = [u for u in URL_RE.findall(line) if _is_link(u)]
    text = strip_dates(line)
    for url in urls:
        text = text.replace(url, "")
    parts = [p for p in re.split(r"\s+by\s+|\s+[|\u2013\u2014-]\s+|,\s+", text) if p.strip(" ()")]
    if not parts:
        return None
    return {
        "name": parts[0].strip(" ()")[:300],
        "issuer": parts[1].strip(" ()")[:200] if len(parts) > 1 else None,
        "issue_date": _iso(issued),
        "credential_url": urls[0] if urls else None,
    }


def _achievement(line: str) -> dict[str, Any]:
    _, achieved, _ = find_range(line)
    # Keep the resume's wording verbatim; dates inside a sentence are part of the claim.
    data: dict[str, Any] = {"title": line[:300], "achieved_on": _iso(achieved)}
    if len(line) > 300:
        data["description"] = line[:5000]
    return data


def _skills(lines: list[str]) -> list[ParsedSkill]:
    skills: list[ParsedSkill] = []
    seen: set[str] = set()
    for line in lines:
        text = strip_bullet(line)
        label, sep, rest = text.partition(":")
        category = None
        if sep and len(label) <= 40:
            text = rest
            lowered = label.lower()
            category = next((c for key, c in SKILL_CATEGORIES if key in lowered), None)
        for name in split_list(text):
            name = re.sub(r"^and\s+", "", name, flags=re.IGNORECASE).strip()
            key = " ".join(name.lower().split())
            if not name or len(name) > 100 or len(name.split()) > 5 or key in seen:
                continue
            seen.add(key)
            skills.append(ParsedSkill(name=name, category=category, source_excerpt=line))
    return skills


# --- Entry point --------------------------------------------------------------------------

_ENTRY_SECTIONS = {
    SectionKind.EDUCATION: (SuggestionSection.EDUCATION, _education, "education entry"),
    SectionKind.EXPERIENCE: (
        SuggestionSection.WORK_EXPERIENCE, _experience, "work experience entry"
    ),
    SectionKind.PROJECTS: (SuggestionSection.PROJECT, _project, "project"),
}  # fmt: skip


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v not in (None, "", False)}


class HeuristicResumeParser:
    name = "heuristic"

    def parse(self, text: str) -> ParsedResume:
        sections = detect_sections(text)
        result = ParsedResume(
            personal=_parse_personal(
                sections.get(SectionKind.HEADER, []), sections.get(SectionKind.SUMMARY, [])
            )
        )
        for kind, (section, parse_entry, label) in _ENTRY_SECTIONS.items():
            for entry in split_entries(sections.get(kind, [])):
                data = parse_entry(entry)
                excerpt = "\n".join(entry.raw)
                if data is None:
                    result.warnings.append(f"Couldn't read a {label}: {excerpt[:120]!r}")
                    continue
                result.items.append(ParsedItem(section, _clean(data), list(entry.bullets), excerpt))
        for line in _single_line_items(sections.get(SectionKind.CERTIFICATIONS, [])):
            if data := _certification(line):
                result.items.append(
                    ParsedItem(SuggestionSection.CERTIFICATION, _clean(data), [], line)
                )
        if YEAR_ONLY_RE.search(text):
            result.warnings.append(
                "Some dates give only a year. Profile dates have months, so those were "
                "recorded as January (starts) or December (ends): correct the months while "
                "reviewing, so nothing more specific than your resume is claimed."
            )
        for line in _single_line_items(sections.get(SectionKind.ACHIEVEMENTS, [])):
            result.items.append(
                ParsedItem(SuggestionSection.ACHIEVEMENT, _clean(_achievement(line)), [], line)
            )
        for line in sections.get(SectionKind.COURSEWORK, []):
            for course in split_list(line):
                if len(course) <= 300:
                    result.items.append(ParsedItem(
                        SuggestionSection.COURSEWORK, {"course_name": course}, [], line
                    ))  # fmt: skip
        result.skills = _skills(sections.get(SectionKind.SKILLS, []))
        return result
