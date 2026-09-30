"""Job discovery end to end: sources, search and filters, detail, import into the job
pipeline (then matching, unchanged), and failure isolation between providers."""

from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.discovery import get_job_sources
from app.api.routes.matching import get_match_llm
from app.core.config import Settings
from app.discovery.access import AccessKind, SourcePolicy, check_response
from app.discovery.models import JobSearchQuery, NormalizedJob
from app.discovery.provider import JobSourceProvider, ProviderError
from app.discovery.providers.mock import MockJobProvider
from app.discovery.registry import ProviderRegistry
from app.jobs.models import Job, JobSource
from app.users.models import User

from .conftest import client_for, make_user
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, post
from .test_tailored_resume_api import build_profile

pytestmark = pytest.mark.anyio
DISCOVERY = "/api/v1/discovery"


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


def _client(
    db: AsyncSession, user: User, embedder: SpyEmbedder, registry: ProviderRegistry | None = None
) -> httpx2.AsyncClient:
    settings = Settings(_env_file=None, app_env="test")
    overrides: dict[Any, Any] = {get_embedder: lambda: embedder, get_match_llm: lambda: None}
    if registry is not None:
        overrides[get_job_sources] = lambda: registry
    return client_for(db, user, settings=settings, overrides=overrides)


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def search(api: httpx2.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await api.get(f"{DISCOVERY}/jobs", params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def titles(results: dict[str, Any]) -> list[str]:
    return [j["title"] for j in results["jobs"]]


# --- Sources and search ----------------------------------------------------------------------


async def test_sources_are_listed_with_their_access_policy(api: httpx2.AsyncClient) -> None:
    response = await api.get(f"{DISCOVERY}/sources")
    assert response.status_code == 200
    [mock] = response.json()
    assert mock["name"] == "mock" and mock["enabled"] and mock["access_kind"] == "mock"


async def test_search_returns_normalized_postings(api: httpx2.AsyncClient) -> None:
    results = await search(api)
    assert results["total"] == 20 and len(results["jobs"]) == 20 and results["errors"] == []
    first = results["jobs"][0]
    for field in (
        "title",
        "company",
        "location",
        "url",
        "source",
        "description",
        "employment_type",
        "work_mode",
        "posted_date",
        "deadline",
        "source_identifier",
        "skills",
        "experience_level",
    ):
        assert field in first, field
    assert first["title"] == "Machine Learning Teaching Assistant"  # newest first
    assert first["imported_job_id"] is None


async def test_pagination(api: httpx2.AsyncClient) -> None:
    everything = titles(await search(api))
    second = await search(api, page=2, page_size=5)
    assert second["total"] == 20 and titles(second) == everything[5:10]
    assert (await search(api, page=5, page_size=5))["jobs"] == []


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {"role": "ML engineer"},
            {"ML Engineer", "Machine Learning Engineer Intern", "Senior Machine Learning Engineer"},
        ),
        ({"location": "pune"}, {"Frontend Developer (React)", "Product Analyst Intern"}),
        ({"remote": "true", "employment_type": "internship"}, {"AI Research Intern"}),
        (
            {"employment_type": ["internship", "part_time"]},
            {
                "Machine Learning Engineer Intern",
                "AI Research Intern",
                "Product Analyst Intern",
                "Mobile Developer (React Native)",
                "Machine Learning Teaching Assistant",
            },
        ),
        ({"skills": ["RAG"]}, {"ML Engineer", "AI Research Intern", "NLP Engineer"}),
        ({"experience_level": "senior"}, {"Senior Machine Learning Engineer"}),
        (
            {"experience_level": ["lead", "senior"]},
            {"Senior Machine Learning Engineer", "Lead Data Engineer"},
        ),
        (
            {"role": "engineer", "remote": "false", "skills": "PyTorch", "location": "Bengaluru"},
            {"Senior Machine Learning Engineer", "Computer Vision Engineer"},
        ),
    ],
)
async def test_filters(api: httpx2.AsyncClient, params: dict[str, Any], expected: set[str]) -> None:
    results = await search(api, **params)
    assert set(titles(results)) == expected
    assert results["total"] == len(expected)


async def test_requested_skills_are_ranked_and_reported(api: httpx2.AsyncClient) -> None:
    results = await search(api, skills=["Docker", "Kubernetes", "AWS"])
    top = results["jobs"][0]
    assert top["matched_skills"] == ["Docker", "Kubernetes", "AWS"]
    counts = [len(j["matched_skills"]) for j in results["jobs"]]
    assert counts == sorted(counts, reverse=True)


async def test_invalid_filters_are_rejected(api: httpx2.AsyncClient) -> None:
    assert (
        await api.get(f"{DISCOVERY}/jobs", params={"employment_type": "gig"})
    ).status_code == 422
    assert (await api.get(f"{DISCOVERY}/jobs", params={"page_size": 500})).status_code == 422
    assert (await api.get(f"{DISCOVERY}/jobs", params={"source": "nope"})).status_code == 404


async def test_posting_detail(api: httpx2.AsyncClient) -> None:
    response = await api.get(f"{DISCOVERY}/jobs/mock/mock-1006")
    assert response.status_code == 200
    posting = response.json()
    assert (posting["title"], posting["company"]) == ("ML Engineer", "Northwind Robotics")
    assert posting["work_mode"] == "remote" and posting["deadline"] == "2026-11-01"
    assert (await api.get(f"{DISCOVERY}/jobs/mock/mock-0000")).status_code == 404
    assert (await api.get(f"{DISCOVERY}/jobs/nope/mock-1006")).status_code == 404


# --- Import into the job pipeline ---------------------------------------------------------------


async def test_import_creates_an_analyzed_job_once(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await build_profile(api)  # a profile, so the job can be matched
    response = await api.post(f"{DISCOVERY}/jobs/mock/mock-1006/import")
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    job = await db.scalar(select(Job).where(Job.id == job_id))
    assert job is not None
    assert (job.source, job.source_name, job.external_id) == (JobSource.OTHER, "mock", "mock-1006")
    assert job.input_method == "discovered"
    assert (job.title, job.company_name) == ("ML Engineer", "Northwind Robotics")
    assert job.workplace_type == "remote" and job.employment_type == "full_time"
    assert str(job.application_deadline) == "2026-11-01"
    assert job.posted_at is not None and job.url == "https://jobs.example.com/mock/1006"

    detail = (await api.get(f"{JOBS}/{job_id}")).json()
    requirements = {r["description"]: r["importance"] for r in detail["requirements"]}
    assert requirements["Experience with Python and SQL."] == "required"

    again = await api.post(f"{DISCOVERY}/jobs/mock/mock-1006/import")
    assert again.status_code == 200 and again.json() == {"job_id": job_id, "created": False}
    [found] = (await search(api, role="ML engineer", remote="true"))["jobs"]
    assert found["imported_job_id"] == job_id

    # The core matching engine works on it unchanged.
    report = await post(api, f"{JOBS}/{job_id}/match")
    assert report["job_title"] == "ML Engineer"
    statuses = {r["requirement"]: r["match_status"] for r in report["requirements"]}
    assert statuses["Experience with Python and SQL."] in ("matched", "partial")


async def test_each_user_imports_a_posting_independently(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    other = await make_user(db, "other@example.test")
    ids = []
    for who in (user, other):
        async with _client(db, who, embedder) as api:
            response = await api.post(f"{DISCOVERY}/jobs/mock/mock-1001/import")
            assert response.status_code == 201, response.text
            ids.append(response.json()["job_id"])
    assert ids[0] != ids[1]
    async with _client(db, other, embedder) as api:
        assert (await api.get(f"{JOBS}/{ids[0]}")).status_code == 404  # still private


# --- Provider isolation ------------------------------------------------------------------------


class Broken(JobSourceProvider):
    name, display_name, job_source = "broken", "Broken API", JobSource.JOB_BOARD_API
    policy = SourcePolicy(
        kind=AccessKind.OFFICIAL_API,
        description="Test",
        terms_url="https://example.com/terms",
        automated_access_permitted=True,
    )

    async def search_jobs(self, query: JobSearchQuery) -> list[NormalizedJob]:
        raise ProviderError("timed out")

    async def get_job(self, source_identifier: str) -> NormalizedJob | None:
        raise ProviderError("timed out")

    def normalize_job(self, raw: Mapping[str, Any]) -> NormalizedJob:
        raise ProviderError("unused")


class Guarded(Broken):
    """A source that answers with a bot challenge: CareerPilot must stop, not work around it."""

    name, display_name = "guarded", "Guarded board"
    calls = 0

    async def search_jobs(self, query: JobSearchQuery) -> list[NormalizedJob]:
        Guarded.calls += 1
        check_response(200, {}, "<html>Please verify you are human (hCaptcha)</html>")
        return []

    async def get_job(self, source_identifier: str) -> NormalizedJob | None:
        check_response(403, {}, "")
        return None


class Unpermitted(Broken):
    name, display_name = "unpermitted", "Site without an API"
    policy = SourcePolicy(kind=AccessKind.OFFICIAL_API, description="Test")


async def test_a_failing_or_guarded_source_never_breaks_the_others(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    settings = Settings(_env_file=None, app_env="test")
    registry = ProviderRegistry([MockJobProvider(), Broken(), Guarded(), Unpermitted()], settings)
    async with _client(db, user, embedder, registry) as api:
        results = await search(api)
        assert results["total"] == 20  # the mock still answers
        assert any("Broken API is unavailable: timed out" in e for e in results["errors"])
        assert any("Guarded board" in e and "CAPTCHA" in e for e in results["errors"])
        assert Guarded.calls == 1  # no retries
        sources = {s["name"]: s for s in results["sources"]}
        assert not sources["unpermitted"]["enabled"]
        assert "don't permit automated access" in " ".join(sources["unpermitted"]["reasons"])
        assert (await api.get(f"{DISCOVERY}/jobs/guarded/x")).status_code == 503
        assert (await api.get(f"{DISCOVERY}/jobs/unpermitted/x")).status_code == 404
