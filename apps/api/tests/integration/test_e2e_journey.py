"""The complete CareerPilot journey, end to end, through the real API and database, with a
real headless browser for the mock application site.

Candidate creation → resume upload → evidence → RAG retrieval → job analysis → semantic
matching → skill gaps → resume tailoring → claim verification → cover letter → application
answers → human approval → mock browser application → application tracking.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.matching import get_match_llm
from app.automation import mock_site
from app.core.config import Settings
from app.users.models import User

from ..resume_files import make_docx
from . import e2e_data as data
from .conftest import client_for
from .test_applications_api import APPS, move, ok
from .test_assisted_applications_api import RUNS, site  # noqa: F401 - the mock site fixture
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, post
from .test_resume_api import RESUMES

pytestmark = pytest.mark.anyio
PROFILE = "/api/v1/profile"


@pytest.fixture
async def api(db: AsyncSession, user: User, site: str) -> AsyncIterator[httpx2.AsyncClient]:  # noqa: F811
    embedder = SpyEmbedder()
    settings = Settings(
        _env_file=None, app_env="test", automation_mock_site_url=site, automation_timeout_ms=10000
    )
    overrides: dict[Any, Any] = {get_embedder: lambda: embedder, get_match_llm: lambda: None}
    async with client_for(db, user, settings=settings, overrides=overrides) as client:
        yield client


@pytest.fixture(autouse=True)
def _clean_site() -> None:
    mock_site.SUBMISSIONS.clear()
    mock_site.QUESTIONS.clear()


def _words(value: Any) -> str:
    """Every string in a document, lowercased (ids excluded)."""
    if isinstance(value, dict):
        return " ".join(_words(v) for k, v in value.items() if not k.endswith(("id", "ids")))
    if isinstance(value, list):
        return " ".join(_words(v) for v in value)
    return str(value).lower() if isinstance(value, str) else ""


async def test_the_complete_journey(api: httpx2.AsyncClient, site: str) -> None:  # noqa: F811
    # --- 1. Candidate creation --------------------------------------------------------------
    profile = await post(
        api, PROFILE, {"full_name": data.CANDIDATE_NAME, "contact_email": data.CANDIDATE_EMAIL}
    )
    candidate_id = profile["id"]

    # --- 2. Resume upload: extracted, then reviewed and accepted by the candidate ------------
    upload = await api.post(
        RESUMES,
        files={"file": ("aarav-mehta.docx", make_docx(data.RESUME_LINES), "application/docx")},
    )
    assert upload.status_code == 201, upload.text
    resume = upload.json()
    assert resume["parse_status"] == "parsed" and resume["pending_suggestions"] > 0
    suggestions: list[dict[str, Any]] = (
        await api.get(f"{PROFILE}/suggestions?resume_id={resume['id']}")
    ).json()
    for s in suggestions:
        await ok(await api.post(f"{PROFILE}/suggestions/{s['id']}/accept", json={}))
    profile = await ok(await api.get(PROFILE))
    project_titles = {p["title"] for p in profile["projects"]}
    assert {"DocuMind - RAG Question Answering", "Crop Disease Classifier"} <= project_titles
    assert len(profile["projects"]) == 4
    assert profile["educations"][0]["degree"].startswith("B.Tech")
    assert len(profile["certifications"]) == 2
    skills = {s["name"].lower() for s in profile["skills"]}
    assert {"python", "fastapi", "react", "pytorch"} <= skills

    # --- 3. Evidence creation: from the resume, plus statements the candidate adds -----------
    for statement in data.EXTRA_EVIDENCE:
        added = await post(
            api, f"{PROFILE}/evidence", {"content": statement, "source_type": "profile"}
        )
        assert added["confirmed_at"]  # entered by the candidate: confirmed
    profile = await ok(await api.get(PROFILE))
    evidence = [
        *profile["evidence"],
        *(
            e
            for key in ("projects", "educations", "certifications")
            for r in profile[key]
            for e in r["evidence"]
        ),
    ]
    confirmed = [e for e in evidence if e["confirmed_at"]]
    assert len(confirmed) >= 10  # every project bullet and statement is confirmed evidence

    # --- 4. RAG retrieval: verified evidence only, most relevant first -----------------------
    hits = await post(
        api,
        "/api/candidate/evidence/search",
        {"candidate_id": candidate_id, "query": "retrieval augmented generation RAG pipeline"},
    )
    assert hits["results"] and "rag" in hits["results"][0]["factual_content"].lower()
    assert all(r["verification_status"] == "verified" for r in hits["results"])
    react = await post(
        api,
        "/api/candidate/evidence/search",
        {"candidate_id": candidate_id, "query": "React frontend for a college club"},
    )
    assert "react" in react["results"][0]["factual_content"].lower()

    # --- 5. Job analysis: four postings, requirements as stated ------------------------------
    jobs: dict[str, dict[str, Any]] = {}
    for key, job in data.JOBS.items():
        analyzed = await post(api, f"{JOBS}/analyze", job)
        assert analyzed["title"] == job["title"]
        required = [r for r in analyzed["requirements"] if r["importance"] == "required"]
        assert required, key
        for r in analyzed["requirements"]:
            assert r["description"].lower() in job["description"].lower() or r["source_excerpt"]
        jobs[key] = analyzed

    # --- 6. Semantic matching and 7. skill gaps ---------------------------------------------
    reports = {k: await post(api, f"{JOBS}/{j['id']}/match") for k, j in jobs.items()}
    coverage = {k: r["scores"]["evidence_coverage"] for k, r in reports.items()}
    assert coverage["ai"] > coverage["frontend"] and coverage["ai"] > coverage["data"]
    assert coverage["ml"] > coverage["data"]
    for report in reports.values():
        for req in report["requirements"]:
            if req["match_status"] in ("matched", "partial"):
                assert req["evidence_ids"], req  # every match cites evidence
    missing = {
        k: " ".join(r["requirement"].lower() for r in rep["missing_skills"])
        for k, rep in reports.items()
    }
    assert "typescript" in missing["frontend"]
    assert "tableau" in missing["data"] or "excel" in missing["data"]
    assert "kubernetes" in missing["ml"]
    assert "python" not in missing["ai"] and "rag" not in missing["ai"]

    # --- 8. Resume tailoring and 9. claim verification --------------------------------------
    ai_job = jobs["ai"]["id"]
    tailored = await post(api, f"{JOBS}/{ai_job}/tailored-resumes")
    assert tailored["status"] == "verified", tailored["notes"]
    assert tailored["report"]["outcome"] == "approved"
    text = _words(tailored["content"])
    assert "documind" in text or "rag" in text
    for claim in [*tailored["content"]["summary"], *tailored["content"]["skills"]]:
        assert claim["evidence_ids"], claim  # every generated claim cites evidence
    stored = (
        await api.get(f"/api/v1/tailored-resumes/{tailored['id']}/verification-reports")
    ).json()
    assert stored and stored[0]["outcome"] == "approved"

    # --- 10. Cover letter --------------------------------------------------------------------
    letter = await post(api, f"{JOBS}/{ai_job}/cover-letters")
    assert letter["status"] == "verified", letter["notes"]
    assert letter["content"]["company_name"] == "Northwind AI"

    # --- 11. Application answers -------------------------------------------------------------
    answers = await post(
        api,
        f"{JOBS}/{ai_job}/application-answers",
        {
            "questions": [
                "Why are you interested in this role?",
                "Describe your experience with Python.",
            ]
        },
    )
    answers = answers if isinstance(answers, list) else answers["answers"]
    assert all(a["status"] == "verified" for a in answers), [a["notes"] for a in answers]
    for a in answers:
        await ok(await api.post(f"/api/v1/application-answers/{a['id']}/approve"))

    # --- 12. Human approval ------------------------------------------------------------------
    application = await post(api, APPS, {"job_id": ai_job})
    app_id = application["id"]
    await ok(
        await api.patch(
            f"{APPS}/{app_id}", json={"application_url": f"{site}/jobs/northwind-ai/apply"}
        )
    )
    await ok(await move(api, app_id, "application_prepared"))
    early = await api.post(f"{APPS}/{app_id}/assisted-runs", json={})
    assert early.status_code == 409  # nothing is filled before the candidate approves
    await ok(await api.post(f"{APPS}/{app_id}/review/request"))
    review = await ok(await api.get(f"{APPS}/{app_id}/review"))
    assert review["approval_state"] == "ready_for_review"
    assert review["resume"]["verification"]["verified"]
    assert review["cover_letter"]["verification"]["verified"]
    assert not [i for i in review["issues"] if i["severity"] == "blocker"], review["issues"]
    approved = await ok(
        await api.post(
            f"{APPS}/{app_id}/approve",
            json={"content_hash": review["content_hash"], "confirm": True},
        )
    )
    assert approved["approval_state"] == "approved"

    # --- 13. Mock browser application --------------------------------------------------------
    started = await api.post(f"{APPS}/{app_id}/assisted-runs", json={})
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["status"] == "needs_input"  # work authorization isn't in the candidate's records
    run = await ok(
        await api.post(f"{RUNS}/{run['id']}/inputs", json={"inputs": {"work_authorization": "Yes"}})
    )
    assert run["status"] == "awaiting_review", run["stop_reason"]
    assert mock_site.SUBMISSIONS == []  # paused: nothing sent yet
    assert run["review"]["personal"]["first_name"] == "Aarav"
    assert run["review"]["personal"]["email"] == data.CANDIDATE_EMAIL
    submitted = await ok(
        await api.post(
            f"{RUNS}/{run['id']}/submit",
            json={"review_hash": run["review_hash"], "confirm": True},
        )
    )
    assert submitted["status"] == "submitted"
    [sent] = mock_site.SUBMISSIONS
    assert sent["fields"]["last_name"] == "Mehta"
    assert sent["files"]["resume"]["sha256"] == run["review"]["resume"]["sha256"]
    assert sent["files"]["cover_letter"]["sha256"] == run["review"]["cover_letter"]["sha256"]

    # --- 14. Application tracking ------------------------------------------------------------
    tracked = await ok(await api.get(f"{APPS}/{app_id}"))
    assert tracked["status"] == "submitted" and tracked["approval_state"] == "submitted"
    assert tracked["applied_at"] and tracked["follow_ups"]
    interviewing = await post(api, f"{APPS}/{app_id}/interviews", {"interview_type": "technical"})
    assert interviewing["status"] == "interview"
    for key in ("ml", "frontend"):
        await post(api, APPS, {"job_id": jobs[key]["id"]})
    dashboard = await ok(await api.get(f"{APPS}/dashboard"))
    assert dashboard["total"] == 3
    assert dashboard["counts"]["interview"] == 1 and dashboard["counts"]["saved"] == 2
    timeline = [e["title"] for e in tracked["timeline"]]
    assert any("Submitted" in t for t in timeline)
