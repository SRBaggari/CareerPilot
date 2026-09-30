"""End-to-end candidate-job matching: retrieval, statuses, grounding, persistence, staleness."""

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIExecutionLog, AIExecutionStatus
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.matching import get_match_llm
from app.core.config import Settings
from app.matching.models import JobMatch, RequirementMatch, SkillGap, requirement_match_evidence
from app.profiles.models import CandidateEvidence, EvidenceOrigin, EvidenceSourceType
from app.users.models import User

from ..test_job_analysis import FakeProvider
from .conftest import client_for, make_user
from .test_evidence_search import SpyEmbedder

pytestmark = pytest.mark.anyio
PROFILE, JOBS = "/api/v1/profile", "/api/v1/jobs"

REQUIREMENTS = [
    ("skill", "required", "Experience with Retrieval-Augmented Generation", None),
    ("technology", "required", "Python", None),
    ("technology", "required", "Docker", None),
    ("technology", "required", "Kubernetes", None),
    ("technology", "preferred", "Terraform", None),
    ("experience", "required", "3+ years of experience building machine learning systems", "3"),
    ("education", "required", "Bachelor's degree in Computer Science", None),
    ("eligibility", "required", "Must be authorized to work in the United States", None),
    ("skill", "required", "Excellent written communication", None),
    ("responsibility", "informational", "Mentor junior engineers", None),
]
EXPECTED = {
    "Experience with Retrieval-Augmented Generation": "matched",
    "Python": "matched",
    "Docker": "matched",
    "Kubernetes": "missing",
    "Terraform": "partial",  # listed in skills, but no evidence shows it
    "3+ years of experience building machine learning systems": "partial",  # ~1 year of dates
    "Bachelor's degree in Computer Science": "matched",
    "Must be authorized to work in the United States": "unknown",
    "Excellent written communication": "unknown",
}


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


def _client(
    db: AsyncSession, user: User, embedder: SpyEmbedder, extra: dict[Any, Any] | None = None
) -> httpx2.AsyncClient:
    settings = Settings(_env_file=None, app_env="test", match_judge="auto")
    overrides = {get_embedder: lambda: embedder, **(extra or {})}
    return client_for(db, user, settings=settings, overrides=overrides)


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def post(
    api: httpx2.AsyncClient, path: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await api.post(path, json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()  # type: ignore[no-any-return]


async def build_profile(api: httpx2.AsyncClient) -> dict[str, str]:
    await post(api, PROFILE, {"full_name": "Test Candidate"})
    project = await post(api, f"{PROFILE}/projects", {"title": "Multi-Agent Research Assistant"})
    job = await post(
        api,
        f"{PROFILE}/work-experiences",
        {
            "title": "Machine Learning Intern",
            "company_name": "Acme Analytics",
            "start_date": "2023-05-01",
            "end_date": "2024-05-01",
        },
    )
    education = await post(
        api,
        f"{PROFILE}/educations",
        {
            "institution": "State University",
            "degree": "B.Tech",
            "degree_level": "bachelor",
            "field_of_study": "Computer Science",
        },
    )
    ids: dict[str, str] = {}
    for key, source, subject, content in (
        (
            "rag",
            "project",
            project["id"],
            "Implemented RAG pipeline in Multi-Agent Research Assistant.",
        ),
        ("python", "project", project["id"], "Built data pipelines in Python and SQL."),
        ("docker", "work_experience", job["id"], "Deployed ML models with Docker on AWS."),
        ("degree", "education", education["id"], "B.Tech in Computer Science, State University."),
    ):
        created = await post(
            api,
            f"{PROFILE}/evidence",
            {"source_type": source, "subject_id": subject, "content": content},
        )
        ids[key] = created["id"]
    await post(api, f"{PROFILE}/skills", {"name": "Terraform"})
    return ids


async def build_job(api: httpx2.AsyncClient) -> str:
    job = await post(
        api,
        JOBS,
        {
            "title": "ML Engineer",
            "company_name": "Northwind",
            "requirements": [
                {"requirement_type": t, "importance": i, "description": d, "min_years": y}
                for t, i, d, y in REQUIREMENTS
            ],
        },
    )
    return job["id"]  # type: ignore[no-any-return]


def statuses(report: dict[str, Any]) -> dict[str, str]:
    return {r["requirement"]: r["match_status"] for r in report["requirements"]}


# --- The report -------------------------------------------------------------------------


async def test_match_report_classifies_every_requirement(api: httpx2.AsyncClient) -> None:
    ids = await build_profile(api)
    report = await post(api, f"{JOBS}/{await build_job(api)}/match")

    assert statuses(report) == EXPECTED
    assert report["informational_not_scored"] == 1
    by_text = {r["requirement"]: r for r in report["requirements"]}

    rag = by_text["Experience with Retrieval-Augmented Generation"]
    assert rag["evidence_ids"] == [ids["rag"]]
    [cited] = rag["matching_candidate_evidence"]
    assert cited["factual_content"] == "Implemented RAG pipeline in Multi-Agent Research Assistant."
    assert cited["source"]["record_label"] == "Multi-Agent Research Assistant"
    assert rag["semantic_similarity"] is not None and "RAG" in rag["explanation"]
    assert rag["requirement_type"] == "skill"

    assert by_text["Python"]["evidence_ids"] == [ids["python"]]
    assert by_text["Docker"]["evidence_ids"] == [ids["docker"]]
    assert by_text["Bachelor's degree in Computer Science"]["evidence_ids"] == [ids["degree"]]
    experience = by_text["3+ years of experience building machine learning systems"]
    assert experience["details"]["required_years"] == 3.0
    assert experience["details"]["candidate_years"] == 1.0
    for status in ("missing", "unknown"):  # nothing is cited for these
        assert all(
            r["evidence_ids"] == [] for r in report["requirements"] if r["match_status"] == status
        )

    assert [r["requirement"] for r in report["missing_skills"]] == ["Kubernetes"]
    assert {r["requirement"] for r in report["partial_matches"]} == {
        "Terraform",
        "3+ years of experience building machine learning systems",
    }
    assert [r["requirement"] for r in report["potentially_disqualifying"]] == [
        "Must be authorized to work in the United States"
    ]
    assert {r["requirement"] for r in report["strongest_matches"]} == {
        "Experience with Retrieval-Augmented Generation",
        "Python",
        "Docker",
        "Bachelor's degree in Computer Science",
    }
    assert report["status_counts"] == {"matched": 4, "partial": 2, "missing": 1, "unknown": 2}


async def test_scores_are_explainable_coverage_not_a_probability(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    report = await post(api, f"{JOBS}/{await build_job(api)}/match")
    # Required (unknowns excluded): RAG, Python, Docker, Bachelor matched (4), experience
    # partial (0.5), Kubernetes missing (0) -> 4.5 / 6. Preferred: Terraform partial -> 0.5.
    assert report["scores"]["required_coverage"] == 0.75
    assert report["scores"]["preferred_coverage"] == 0.5
    assert report["scores"]["evidence_coverage"] == round((4.5 + 0.25) / 6.5, 4)
    assert "not a prediction or guarantee" in report["disclaimer"]
    assert "probability" not in str(report["scores"]).lower()


# --- Grounding --------------------------------------------------------------------------


async def test_cited_evidence_is_always_the_candidates_verified_evidence(
    api: httpx2.AsyncClient, db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    candidate = (await api.get(PROFILE)).json()["id"]

    # Unverified evidence naming Kubernetes, and another candidate's Kubernetes evidence.
    db.add(
        CandidateEvidence(
            candidate_profile_id=candidate,
            source_type=EvidenceSourceType.PROFILE,
            origin=EvidenceOrigin.RESUME_EXTRACTED,
            confirmed_at=None,
            content="Deployed services on Kubernetes clusters.",
        )
    )
    await db.flush()
    other = await make_user(db, "other@example.test")
    async with _client(db, other, embedder) as other_api:
        await post(other_api, PROFILE, {"full_name": "Other Candidate"})
        await post(
            other_api,
            f"{PROFILE}/evidence",
            {"source_type": "profile", "content": "Ran Kubernetes in production for 4 years."},
        )

    report = await post(api, f"{JOBS}/{job_id}/match")
    assert statuses(report)["Kubernetes"] == "missing"
    verified = {
        str(i)
        for i in await db.scalars(
            select(CandidateEvidence.id).where(
                CandidateEvidence.candidate_profile_id == candidate,
                CandidateEvidence.verification_status == "verified",
            )
        )
    }
    cited = {i for r in report["requirements"] for i in r["evidence_ids"]}
    assert cited and cited <= verified


async def test_llm_judge_output_is_grounded_and_logged(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    class Judge(FakeProvider):
        """Claims Kubernetes with an invented citation and flips the eligibility check."""

        async def complete_json(
            self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
        ) -> Any:
            import json
            import re

            from app.ai.provider import LLMJsonResult

            found = re.search(r"<requirements>\n(.*)\n</requirements>", prompt, re.S)
            assert found is not None
            requirements = json.loads(found.group(1))
            assessments = []
            for r in requirements:
                ids = [e["evidence_id"] for e in r["evidence"]]
                if r["requirement"] == "Kubernetes":
                    assessments.append(
                        {
                            "requirement_id": r["requirement_id"],
                            "status": "matched",
                            "explanation": "Invented.",
                            "evidence_ids": ["not-an-id"],
                        }
                    )
                elif "authorized" in r["requirement"]:
                    assessments.append(
                        {
                            "requirement_id": r["requirement_id"],
                            "status": "matched",
                            "explanation": "Surely.",
                            "evidence_ids": ids[:1],
                        }
                    )
                elif r["requirement"] == "Python":
                    assessments.append(
                        {
                            "requirement_id": r["requirement_id"],
                            "status": "matched",
                            "explanation": "Your evidence shows Python data pipelines.",
                            "evidence_ids": ids,
                        }
                    )
            return LLMJsonResult({"assessments": assessments}, "fake", "fake-model", 300, 90, 12)

    async with _client(db, user, embedder, {get_match_llm: lambda: Judge({})}) as api:
        await build_profile(api)
        report = await post(api, f"{JOBS}/{await build_job(api)}/match")
    got = {r["requirement"]: r for r in report["requirements"]}
    assert report["matcher"] == "llm:fake-model"
    assert got["Kubernetes"]["match_status"] == "missing"  # unsupported claim rejected
    assert got["Must be authorized to work in the United States"]["match_status"] == "unknown"
    python = got["Python"]
    assert python["judge"] == "llm:fake-model"
    assert python["explanation"] == "Your evidence shows Python data pipelines."
    assert len(python["evidence_ids"]) >= 1
    log = await db.scalar(select(AIExecutionLog))
    assert (
        log is not None and log.status == AIExecutionStatus.SUCCESS and log.operation == "matching"
    )


# --- Persistence, staleness, recompute --------------------------------------------------


async def test_match_is_stored_and_recomputed_in_place(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    ids = await build_profile(api)
    job_id = await build_job(api)
    first = await post(api, f"{JOBS}/{job_id}/match")

    assert await db.scalar(select(func.count()).select_from(JobMatch)) == 1
    assert await db.scalar(select(func.count()).select_from(RequirementMatch)) == 9
    links = await db.scalar(select(func.count()).select_from(requirement_match_evidence))
    assert links == sum(len(r["evidence_ids"]) for r in first["requirements"])
    gaps = {g.severity for g in await db.scalars(select(SkillGap))}
    assert gaps == {"significant", "minor"}  # Kubernetes (required, missing); partials

    fetched = (await api.get(f"{JOBS}/{job_id}/match")).json()
    assert fetched["is_stale"] is False and statuses(fetched) == statuses(first)

    edited = await api.patch(
        f"{PROFILE}/evidence/{ids['docker']}",
        json={"content": "Deployed ML models with Docker and Kubernetes on AWS."},
    )
    assert edited.status_code == 200
    assert (await api.get(f"{JOBS}/{job_id}/match")).json()["is_stale"] is True

    second = await post(api, f"{JOBS}/{job_id}/match")
    assert second["is_stale"] is False
    assert statuses(second)["Kubernetes"] == "matched"
    assert await db.scalar(select(func.count()).select_from(JobMatch)) == 1


async def test_deleted_evidence_drops_out_of_the_stored_report(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    ids = await build_profile(api)
    job_id = await build_job(api)
    await post(api, f"{JOBS}/{job_id}/match")
    await db.execute(text("DELETE FROM candidate_evidence WHERE id = :id"), {"id": ids["python"]})
    report = (await api.get(f"{JOBS}/{job_id}/match")).json()
    assert report["is_stale"] is True
    assert ids["python"] not in {i for r in report["requirements"] for i in r["evidence_ids"]}


async def test_empty_profile_gets_a_warning(api: httpx2.AsyncClient) -> None:
    await post(api, PROFILE, {"full_name": "New Candidate"})
    report = await post(api, f"{JOBS}/{await build_job(api)}/match")
    assert any("no verified evidence" in w for w in report["warnings"])
    assert report["status_counts"]["matched"] == 0


# --- Access -----------------------------------------------------------------------------


async def test_access_rules(
    api: httpx2.AsyncClient, db: AsyncSession, embedder: SpyEmbedder
) -> None:
    job_id = await build_job(api)
    assert (await api.post(f"{JOBS}/{job_id}/match")).status_code == 404  # no profile yet
    await post(api, PROFILE, {"full_name": "Test Candidate"})
    assert (await api.get(f"{JOBS}/{job_id}/match")).status_code == 404  # not computed yet
    other = await make_user(db, "other@example.test")
    async with _client(db, other, embedder) as other_api:
        await post(other_api, PROFILE, {"full_name": "Other Candidate"})
        assert (await other_api.post(f"{JOBS}/{job_id}/match")).status_code == 404
