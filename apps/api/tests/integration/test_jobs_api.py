"""Job analysis API: analysis, storage, manual entry, privacy, and the LLM path."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIExecutionLog, AIExecutionStatus
from app.ai.provider import LLMError, LLMJsonResult
from app.api.routes.jobs import get_job_llm
from app.core.config import Settings
from app.documents.models import TailoredResume
from app.jobs.models import Job, JobRequirement
from app.profiles.models import CandidateProfile, Skill
from app.users.models import User

from ..test_job_analysis import FakeProvider, llm_output
from .conftest import client_for, make_user

pytestmark = pytest.mark.anyio
JOBS = "/api/v1/jobs"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "jobs"


def sample(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text("utf-8")


@pytest.fixture
async def api(db: AsyncSession, user: User) -> AsyncIterator[httpx2.AsyncClient]:
    settings = Settings(_env_file=None, app_env="test", job_analyzer="auto")
    async with client_for(db, user, settings=settings) as client:
        yield client


async def _analyze(api: httpx2.AsyncClient, name: str, **extra: Any) -> dict[str, Any]:
    response = await api.post(f"{JOBS}/analyze", json={"description": sample(name), **extra})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _by(job: dict[str, Any], importance: str, kind: str) -> list[str]:
    return [r["description"] for r in job["requirements"]
            if r["importance"] == importance and r["requirement_type"] == kind]  # fmt: skip


# --- Analysis ---------------------------------------------------------------------------


async def test_analyze_stores_the_job_and_its_requirements(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    job = await _analyze(api, "ml_engineer_us", source_url="careers.northwind.example/jobs/42")

    assert (job["title"], job["company_name"], job["location"]) == (
        "Senior Machine Learning Engineer", "Northwind Robotics", "Austin, TX",
    )  # fmt: skip
    assert (job["workplace_type"], job["employment_type"]) == ("remote", "full_time")
    assert job["salary"]["minimum"] == "160000.00" and job["salary"]["currency"] is None
    assert job["application_deadline"] == "2026-07-31"
    assert job["source_url"] == "https://careers.northwind.example/jobs/42"
    assert (job["input_method"], job["analyzer_name"]) == ("pasted_text", "heuristic")
    assert _by(job, "required", "technology") == [
        "Machine Learning",
        "Python",
        "SQL",
        "Docker",
        "AWS",
    ]
    assert _by(job, "preferred", "technology") == ["Terraform"]
    counts = job["requirement_counts"]
    assert counts["required"] > 0 and counts["preferred"] > 0 and counts["informational"] > 0
    assert all(r["source_excerpt"] for r in job["requirements"])

    # Persisted in PostgreSQL, with technologies linked to the shared skill vocabulary.
    db.expunge_all()
    stored = await db.scalar(select(Job).where(Job.id == job["id"]))
    assert stored is not None and stored.description is not None
    assert "Northwind builds perception software" in stored.description
    requirement_count = await db.scalar(
        select(func.count()).select_from(JobRequirement).where(JobRequirement.job_id == stored.id)
    )
    assert requirement_count == len(job["requirements"])
    python = next(r for r in job["requirements"] if r["description"] == "Python")
    skill = await db.get(Skill, python["skill_id"])
    assert skill is not None and skill.normalized_name == "python"
    assert (await api.get(f"{JOBS}/{job['id']}")).json() == job


async def test_internship_sample(api: httpx2.AsyncClient) -> None:
    job = await _analyze(api, "data_intern_india")
    assert (job["workplace_type"], job["employment_type"]) == ("hybrid", "internship")
    assert job["salary"] == {
        "text": "Stipend: Rs. 40,000 per month", "minimum": "40000.00", "maximum": None,
        "currency": "INR", "period": "month",
    }  # fmt: skip
    assert len(_by(job, "required", "eligibility")) == 2


async def test_missing_title_and_company_must_be_provided(api: httpx2.AsyncClient) -> None:
    text = (
        "We are looking for someone to join our analytics team.\n"
        "Requirements\n- 2+ years of experience with SQL.\n- Strong Python skills.\n"
    )
    response = await api.post(f"{JOBS}/analyze", json={"description": text})
    assert response.status_code == 422
    fields = {e["loc"][-1] for e in response.json()["detail"]}
    assert fields == {"title", "company_name"}
    assert (await api.get(JOBS)).json() == []  # nothing stored

    response = await api.post(
        f"{JOBS}/analyze",
        json={"description": text, "title": "Data Analyst", "company_name": "Contoso"},
    )
    assert response.status_code == 201
    job = response.json()
    assert (job["title"], job["company_name"]) == ("Data Analyst", "Contoso")
    assert _by(job, "required", "experience") == ["2+ years of experience with SQL."]


async def test_user_corrections_override_extraction(api: httpx2.AsyncClient) -> None:
    job = await _analyze(api, "startup_prose", title="Senior Backend Developer", location="Remote")
    assert (job["title"], job["company_name"], job["location"]) == (
        "Senior Backend Developer", "Lumen Health", "Remote",
    )  # fmt: skip


async def test_job_urls_are_never_fetched(
    api: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_network(*_: Any, **__: Any) -> None:
        raise AssertionError("the job URL must not be fetched")

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", no_network)
    job = await _analyze(api, "startup_prose", source_url="https://jobs.example.test/123")
    assert job["source_url"] == "https://jobs.example.test/123"


@pytest.mark.parametrize(
    "body",
    [{"description": "Too short"}, {"description": "x" * 60, "source_url": "javascript:alert(1)"},
     {"description": "x" * 60, "scrape": True}],
    ids=["short", "unsafe-url", "unknown-field"],
)  # fmt: skip
async def test_analyze_validation(api: httpx2.AsyncClient, body: dict[str, Any]) -> None:
    assert (await api.post(f"{JOBS}/analyze", json=body)).status_code == 422


# --- Manual entry -----------------------------------------------------------------------


async def test_manual_entry_is_stored_exactly_as_given(api: httpx2.AsyncClient) -> None:
    body = {
        "title": "Frontend Engineer", "company_name": "Fabrikam", "workplace_type": "onsite",
        "employment_type": "contract", "salary_min": "50", "salary_max": "70",
        "salary_currency": "usd", "salary_period": "hour", "application_deadline": "2026-10-01",
        "requirements": [
            {"requirement_type": "technology", "importance": "required", "description": "React"},
            {"requirement_type": "experience", "importance": "preferred",
             "description": "3 years building design systems", "min_years": "3"},
        ],
    }  # fmt: skip
    response = await api.post(JOBS, json=body)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["input_method"] == "manual_entry" and job["analyzer_name"] is None
    assert job["salary"]["currency"] == "USD" and job["salary"]["period"] == "hour"
    assert [(r["description"], r["importance"]) for r in job["requirements"]] == [
        ("React", "required"), ("3 years building design systems", "preferred"),
    ]  # fmt: skip
    assert job["requirements"][0]["skill_id"] is not None


@pytest.mark.parametrize(
    "body",
    [{"company_name": "X"}, {"title": "X", "company_name": "Y", "salary_min": 10, "salary_max": 5},
     {"title": "X", "company_name": "Y", "salary_currency": "dollars"},
     {"title": "X", "company_name": "Y",
      "requirements": [{"requirement_type": "skill", "importance": "critical",
                        "description": "Z"}]}],
    ids=["no-title", "salary-range", "currency", "importance"],
)  # fmt: skip
async def test_manual_entry_validation(api: httpx2.AsyncClient, body: dict[str, Any]) -> None:
    assert (await api.post(JOBS, json=body)).status_code == 422


# --- Listing, privacy, deletion ---------------------------------------------------------


async def test_list_get_delete(api: httpx2.AsyncClient) -> None:
    first = await _analyze(api, "ml_engineer_us")
    second = await _analyze(api, "data_intern_india")
    listed = {j["id"]: j for j in (await api.get(JOBS)).json()}
    # (Order is newest-first; both rows share one transaction timestamp in this test.)
    assert set(listed) == {first["id"], second["id"]}
    assert listed[second["id"]]["requirement_counts"] == second["requirement_counts"]
    assert (await api.delete(f"{JOBS}/{first['id']}")).status_code == 204
    assert (await api.get(f"{JOBS}/{first['id']}")).status_code == 404
    assert [j["id"] for j in (await api.get(JOBS)).json()] == [second["id"]]


async def test_jobs_are_private(api: httpx2.AsyncClient, db: AsyncSession) -> None:
    job = await _analyze(api, "ml_engineer_us")
    other = await make_user(db, "other@example.test")
    async with client_for(db, other) as other_api:
        assert (await other_api.get(JOBS)).json() == []
        assert (await other_api.get(f"{JOBS}/{job['id']}")).status_code == 404
        assert (await other_api.delete(f"{JOBS}/{job['id']}")).status_code == 404


async def test_jobs_with_generated_documents_cannot_be_deleted(
    api: httpx2.AsyncClient, db: AsyncSession, user: User
) -> None:
    job = await _analyze(api, "ml_engineer_us")
    profile = CandidateProfile(user_id=user.id, full_name="Test Candidate")
    db.add(profile)
    await db.flush()
    db.add(TailoredResume(candidate_profile_id=profile.id, job_id=job["id"], content={}))
    await db.flush()
    response = await api.delete(f"{JOBS}/{job['id']}")
    assert response.status_code == 409
    assert (await api.get(f"{JOBS}/{job['id']}")).status_code == 200


# --- LLM path ---------------------------------------------------------------------------


class FailingProvider(FakeProvider):
    async def complete_json(self, **_: Any) -> LLMJsonResult:
        raise LLMError("The AI provider is rate limiting requests.")


async def test_llm_analysis_is_grounded_capped_and_logged(db: AsyncSession, user: User) -> None:
    provider = FakeProvider(llm_output())
    async with client_for(db, user, overrides={get_job_llm: lambda: provider}) as api:
        job = await _analyze(api, "ml_engineer_us")
    assert job["analyzer_name"] == "llm:fake-model"
    descriptions = {r["description"]: r["importance"] for r in job["requirements"]}
    assert "Expert in Rust" not in descriptions
    assert descriptions["Mentor junior engineers."] == "informational"
    assert descriptions["Terraform"] == "preferred"
    assert job["salary"]["currency"] is None
    assert any("discarded" in w for w in job["analysis_warnings"])
    log = await db.scalar(select(AIExecutionLog))
    assert log is not None and log.status == AIExecutionStatus.SUCCESS
    assert log.operation == "job_analysis" and log.input_tokens == 120


async def test_llm_failure_falls_back_to_rules(db: AsyncSession, user: User) -> None:
    async with client_for(db, user, overrides={get_job_llm: lambda: FailingProvider({})}) as api:
        job = await _analyze(api, "ml_engineer_us")
    assert job["analyzer_name"] == "heuristic"
    assert any("AI analysis failed" in w for w in job["analysis_warnings"])
    assert len(job["requirements"]) > 0
