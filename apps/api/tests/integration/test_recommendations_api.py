"""Recommendations end to end over the mock job source: the pipeline, explanations,
eligibility filtering, preferences, and the actions (save, ignore, analyze, apply)."""

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.discovery import get_job_sources
from app.api.routes.matching import get_match_llm
from app.applications.models import Application
from app.core.config import Settings
from app.discovery.providers.mock import MockJobProvider
from app.discovery.registry import ProviderRegistry
from app.jobs.models import Job
from app.users.models import User

from .conftest import client_for, make_user
from .test_discovery_api import Broken
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, PROFILE, post
from .test_tailored_resume_api import build_profile

pytestmark = pytest.mark.anyio
RECS = "/api/v1/recommendations"


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


def _client(
    db: AsyncSession, user: User, embedder: SpyEmbedder, registry: ProviderRegistry | None = None
) -> httpx2.AsyncClient:
    overrides: dict[Any, Any] = {get_embedder: lambda: embedder, get_match_llm: lambda: None}
    if registry is not None:
        overrides[get_job_sources] = lambda: registry
    return client_for(
        db, user, settings=Settings(_env_file=None, app_env="test"), overrides=overrides
    )


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def refresh(api: httpx2.AsyncClient) -> dict[str, Any]:
    response = await api.post(f"{RECS}/refresh")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def view(api: httpx2.AsyncClient, name: str) -> dict[str, Any]:
    response = await api.get(RECS, params={"view": name})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def by_title(listing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["title"]: r for r in listing["recommendations"]}


# --- The pipeline and its explanations -------------------------------------------------------


async def test_recommendations_are_explained(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    listing = await refresh(api)
    recs = listing["recommendations"]
    assert recs and listing["errors"] == []
    assert [r["rank"] for r in recs] == list(range(1, len(recs) + 1))
    coverages = [r["required_coverage"] or 0 for r in recs]
    assert coverages == sorted(coverages, reverse=True)
    for rec in recs:
        assert rec["eligible"] and rec["exclusions"] == []
        explanation = rec["explanation"]
        for field in ("title", "company", "location"):
            assert rec[field]
        assert explanation["summary"].startswith("Recommended because your evidence covers")
        assert explanation["reasons"] and explanation["requirements"]
        assert explanation["required_met"] + explanation["required_partial"] >= 1

    ml = by_title(listing)["ML Engineer"]
    matched = {s["skill"] for s in ml["explanation"]["matched_skills"]}
    assert {"Python", "SQL", "Docker", "AWS"} <= matched
    assert {s["skill"] for s in ml["explanation"]["missing_skills"]} == {"Kubernetes"}
    assert any("Docker" in s["explanation"] for s in ml["explanation"]["matched_skills"])
    projects = {p["title"]: p for p in ml["explanation"]["relevant_projects"]}
    assert "Multi-Agent Research Assistant" in projects
    assert projects["Multi-Agent Research Assistant"]["evidence"]
    assert any(
        "Multi-Agent Research Assistant project is relevant" in r
        for r in ml["explanation"]["reasons"]
    )


async def test_ineligible_jobs_are_filtered_out_with_reasons(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    listing = await refresh(api)
    assert "Senior Machine Learning Engineer" not in by_title(listing)
    filtered = by_title(await view(api, "filtered_out"))
    senior = filtered["Senior Machine Learning Engineer"]
    assert not senior["eligible"] and senior["rank"] is None
    assert any("Requires 5+ years of experience" in e for e in senior["exclusions"])
    assert any("7+ years" in e for e in filtered["Lead Data Engineer"]["exclusions"])
    frontend = filtered["Frontend Developer (React)"]
    assert "doesn't cover any of the job's required requirements" in " ".join(
        frontend["exclusions"]
    )
    assert listing["counts"]["recommended"] + listing["counts"]["filtered_out"] == 20


async def test_preferences_shape_discovery_filtering_and_reasons(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    response = await api.patch(
        PROFILE,
        json={
            "preferred_roles": ["ML Engineer"],
            "work_modes": ["remote", "hybrid"],
            "job_types": ["full_time", "internship"],
            "experience_level": "entry_level",
            "preferred_locations": ["Hyderabad"],
        },
    )
    assert response.status_code == 200, response.text
    listing = await refresh(api)
    titles = set(by_title(listing))
    # Discovery searched the preferred role; the senior role is filtered out on experience.
    assert titles == {"ML Engineer", "Machine Learning Engineer Intern"}
    ml = by_title(listing)["ML Engineer"]
    assert "Matches your preferred role “ML Engineer”." in ml["explanation"]["preference_fit"]
    assert "Remote, as you prefer." in ml["explanation"]["preference_fit"]
    assert "At your experience level (entry level)." in ml["explanation"]["preference_fit"]
    intern = by_title(listing)["Machine Learning Engineer Intern"]
    assert (
        "In Hyderabad, India, one of your preferred locations."
        in (intern["explanation"]["preference_fit"])
    )
    assert "Hybrid, as you prefer." in intern["explanation"]["preference_fit"]


async def test_an_empty_profile_gets_no_recommendations(api: httpx2.AsyncClient) -> None:
    await post(api, PROFILE, {"full_name": "New Candidate"})
    listing = await refresh(api)
    assert listing["recommendations"] == []
    assert listing["counts"]["filtered_out"] == 20


# --- Actions ------------------------------------------------------------------------------


async def test_save_ignore_and_restore(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    recs = by_title(await refresh(api))
    ml, analyst = recs["ML Engineer"], recs["Data Analyst"]

    saved = (await api.post(f"{RECS}/{ml['id']}/save")).json()["recommendation"]
    assert saved["status"] == "saved"
    await api.post(f"{RECS}/{analyst['id']}/ignore")

    listing = await refresh(api)  # choices survive a refresh
    assert "Data Analyst" not in by_title(listing)
    assert by_title(listing)["ML Engineer"]["status"] == "saved"
    assert set(by_title(await view(api, "saved"))) == {"ML Engineer"}
    assert set(by_title(await view(api, "ignored"))) == {"Data Analyst"}
    assert listing["counts"]["ignored"] == 1

    await api.post(f"{RECS}/{analyst['id']}/restore")
    assert "Data Analyst" in by_title(await view(api, "recommended"))


async def test_analyze_imports_the_job_once(api: httpx2.AsyncClient, db: AsyncSession) -> None:
    await build_profile(api)
    ml = by_title(await refresh(api))["ML Engineer"]
    result = (await api.post(f"{RECS}/{ml['id']}/analyze")).json()
    job_id = result["job_id"]
    assert job_id and result["recommendation"]["job_id"] == job_id
    job = await db.get(Job, job_id)
    assert job is not None and job.input_method == "discovered" and job.external_id == "mock-1006"
    assert (await api.get(f"{JOBS}/{job_id}")).json()["requirements"]
    again = (await api.post(f"{RECS}/{ml['id']}/analyze")).json()
    assert again["job_id"] == job_id  # the same job
    # Tailor Resume uses the imported job: the existing resume pipeline takes over.
    resume = await api.post(f"{JOBS}/{job_id}/tailored-resumes")
    assert resume.status_code == 201, resume.text


async def test_start_application_tracks_it_without_submitting(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await build_profile(api)
    ml = by_title(await refresh(api))["ML Engineer"]
    result = (await api.post(f"{RECS}/{ml['id']}/start-application")).json()
    assert result["application_id"] and result["job_id"]
    rec = result["recommendation"]
    assert rec["application"] == {"id": result["application_id"], "status": "analyzed"}
    assert rec["status"] == "saved"
    application = await db.scalar(
        select(Application).where(Application.id == result["application_id"])
    )
    assert application is not None
    assert application.status == "analyzed" and application.approved_at is None
    assert application.discovered_at is not None
    assert application.submitted_at is None
    again = (await api.post(f"{RECS}/{ml['id']}/start-application")).json()
    assert again["application_id"] == result["application_id"]
    # The job now has an application, so it can't be deleted from under it.
    assert (await api.delete(f"{JOBS}/{result['job_id']}")).status_code == 409


async def test_recommendations_go_stale_when_the_profile_changes(api: httpx2.AsyncClient) -> None:
    ids = await build_profile(api)
    await refresh(api)
    await api.delete(f"{PROFILE}/evidence/{ids['latency']}")
    listing = await view(api, "recommended")
    assert listing["recommendations"] and all(r["is_stale"] for r in listing["recommendations"])
    assert not any(r["is_stale"] for r in (await refresh(api))["recommendations"])


async def test_a_failing_source_is_reported(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    registry = ProviderRegistry(
        [MockJobProvider(), Broken()], Settings(_env_file=None, app_env="test")
    )
    async with _client(db, user, embedder, registry) as api:
        await build_profile(api)
        listing = await refresh(api)
    assert listing["recommendations"]
    assert listing["errors"] == ["Broken API is unavailable: timed out"]


async def test_recommendations_are_private(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    async with _client(db, user, embedder) as api:
        await build_profile(api)
        [rec, *_] = (await refresh(api))["recommendations"]
    other = await make_user(db, "other@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Other"})
        assert (await view(stranger, "recommended"))["recommendations"] == []
        for action in ("save", "ignore", "restore", "analyze", "start-application"):
            assert (await stranger.post(f"{RECS}/{rec['id']}/{action}")).status_code == 404
