"""A mock job source for development and tests: 20 fixed sample postings, no network.

Its raw format deliberately differs from ``NormalizedJob`` (as any real API's would), so
``normalize_job`` does the same mapping work a real adapter does.
"""

import json
from collections.abc import Mapping
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from app.discovery.access import AccessKind, SourcePolicy
from app.discovery.models import JobSearchQuery, NormalizedJob, WorkMode
from app.discovery.provider import JobSourceProvider, ProviderError
from app.jobs.models import JobSource
from app.profiles.models import EmploymentType, ExperienceLevel

FIXTURE = Path(__file__).with_name("mock_jobs.json")

_ARRANGEMENTS = {"REMOTE": WorkMode.REMOTE, "HYBRID": WorkMode.HYBRID, "ON_SITE": WorkMode.ONSITE}
_CONTRACTS = {
    "FULL_TIME": EmploymentType.FULL_TIME,
    "PART_TIME": EmploymentType.PART_TIME,
    "INTERN": EmploymentType.INTERNSHIP,
    "CONTRACT": EmploymentType.CONTRACT,
}
_LEVELS = {
    "intern": ExperienceLevel.STUDENT,
    "entry": ExperienceLevel.ENTRY_LEVEL,
    "junior": ExperienceLevel.JUNIOR,
    "mid": ExperienceLevel.MID_LEVEL,
    "senior": ExperienceLevel.SENIOR,
    "lead": ExperienceLevel.LEAD,
}


@cache
def _raw_postings() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _date(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


class MockJobProvider(JobSourceProvider):
    name = "mock"
    display_name = "Sample jobs (development)"
    policy = SourcePolicy(
        kind=AccessKind.MOCK,
        description="Built-in sample postings for development and testing. No network access.",
    )
    job_source = JobSource.OTHER

    async def search_jobs(self, query: JobSearchQuery) -> list[NormalizedJob]:
        return [self.normalize_job(raw) for raw in _raw_postings()]

    async def get_job(self, source_identifier: str) -> NormalizedJob | None:
        for raw in _raw_postings():
            if raw["id"] == source_identifier:
                return self.normalize_job(raw)
        return None

    def normalize_job(self, raw: Mapping[str, Any]) -> NormalizedJob:
        try:
            where = raw.get("where") or {}
            city, country = where.get("city"), where.get("country")
            location = ", ".join(p for p in (city, country) if p) or None
            return NormalizedJob(
                source=self.name,
                source_identifier=str(raw["id"]),
                title=str(raw["position"]).strip(),
                company=str(raw["org"]["name"]).strip(),
                location=location,
                url=raw.get("link"),
                description=str(raw["body"]),
                employment_type=_CONTRACTS.get(str(raw.get("contract"))),
                work_mode=_ARRANGEMENTS.get(str(where.get("arrangement"))),
                posted_date=_date(raw.get("published")),
                deadline=_date(raw.get("apply_by")),
                skills=[str(t) for t in raw.get("tags") or []],
                experience_level=_LEVELS.get(str(raw.get("level"))),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Malformed mock posting: {exc}") from exc
