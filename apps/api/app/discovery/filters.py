"""Filtering applied by the core to every provider's results, so filters behave the same
whatever the source."""

import re
from datetime import date

from app.discovery.models import JobSearchQuery, NormalizedJob, WorkMode
from app.jobs.analysis.vocabulary import find_technologies
from app.profiles.models import EmploymentType, ExperienceLevel

_LEVEL_WORDS: tuple[tuple[ExperienceLevel, re.Pattern[str]], ...] = (
    (ExperienceLevel.LEAD, re.compile(r"\b(lead|principal|staff|head of)\b", re.I)),
    (ExperienceLevel.SENIOR, re.compile(r"\b(senior|sr\.?)\b", re.I)),
    (ExperienceLevel.MID_LEVEL, re.compile(r"\b(mid[- ]level|intermediate)\b", re.I)),
    (ExperienceLevel.JUNIOR, re.compile(r"\b(junior|jr\.?)\b", re.I)),
    (ExperienceLevel.ENTRY_LEVEL, re.compile(r"\b(entry[- ]level|graduate|new grad)\b", re.I)),
    (ExperienceLevel.STUDENT, re.compile(r"\b(intern|internship|trainee|student)\b", re.I)),
)
# Common role abbreviations, so "ML engineer" finds "Machine Learning Engineer".
_ROLE_ALIASES = {
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "swe": "software engineer",
    "sde": "software development engineer",
    "nlp": "natural language processing",
    "cv": "computer vision",
}


def infer_level(job: NormalizedJob) -> ExperienceLevel | None:
    """The level the title states, if any (an internship is a student role)."""
    for level, pattern in _LEVEL_WORDS:
        if pattern.search(job.title):
            return level
    if job.employment_type == EmploymentType.INTERNSHIP:
        return ExperienceLevel.STUDENT
    return None


def enrich(job: NormalizedJob) -> NormalizedJob:
    """Fill skills and experience level from the posting when the provider didn't."""
    skills = list(
        dict.fromkeys([*job.skills, *find_technologies(f"{job.title}\n{job.description}")])
    )
    return job.model_copy(
        update={"skills": skills, "experience_level": job.experience_level or infer_level(job)}
    )


def _words(text: str) -> str:
    text = f" {' '.join(re.findall(r'[a-z0-9+#]+', text.lower()))} "
    for short, long in _ROLE_ALIASES.items():
        text = text.replace(f" {long} ", f" {long} {short} ")
    return text


def _role_matches(role: str, title: str) -> bool:
    haystack = _words(title)
    return all(f" {w} " in haystack for w in _words(role).split())


def matched_skills(job: NormalizedJob, skills: list[str]) -> list[str]:
    have = {s.lower() for s in job.skills}
    return [s for s in skills if s.lower() in have]


def matches(job: NormalizedJob, query: JobSearchQuery) -> bool:
    if query.role and not _role_matches(query.role, job.title):
        return False
    if query.location and query.location.lower() not in (job.location or "").lower():
        return False
    if query.remote is True and job.work_mode != WorkMode.REMOTE:
        return False
    if query.remote is False and job.work_mode == WorkMode.REMOTE:
        return False
    if query.employment_types and job.employment_type not in query.employment_types:
        return False
    if query.experience_levels and job.experience_level not in query.experience_levels:
        return False
    return not query.skills or bool(matched_skills(job, query.skills))


def apply(jobs: list[NormalizedJob], query: JobSearchQuery) -> list[NormalizedJob]:
    """Filter, then sort: most requested skills first, then newest, then title."""
    kept = [j for j in jobs if matches(j, query)]
    return sorted(
        kept,
        key=lambda j: (
            -len(matched_skills(j, query.skills)),
            -(j.posted_date or date.min).toordinal(),
            j.title.lower(),
        ),
    )
