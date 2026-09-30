"""End-to-end resume ingestion: upload -> extraction -> suggestions -> review -> profile."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIExecutionLog, AIExecutionStatus
from app.ai.provider import LLMError, LLMJsonResult
from app.api.routes.resumes import get_resume_llm
from app.core.config import Settings
from app.profiles.models import (
    CandidateEvidence,
    EvidenceOrigin,
    ProfileSuggestion,
    Resume,
    candidate_evidence_skills,
)
from app.users.models import User

from ..resume_files import SAMPLE_LINES, make_docx, make_pdf
from ..test_resume_parsing import LLM_OUTPUT, FakeProvider
from .conftest import client_for, make_user

pytestmark = pytest.mark.anyio

PROFILE = "/api/v1/profile"
RESUMES = "/api/v1/resumes"
PDF = make_pdf(SAMPLE_LINES)
DOCX = make_docx(SAMPLE_LINES)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, app_env="test", storage_dir=str(tmp_path), resume_parser="auto")


@pytest.fixture
async def api(
    db: AsyncSession, user: User, settings: Settings
) -> AsyncIterator[httpx2.AsyncClient]:
    async with client_for(db, user, settings=settings) as client:
        yield client


async def _profile(api: httpx2.AsyncClient, **fields: Any) -> dict[str, Any]:
    response = await api.post(PROFILE, json={"full_name": "Priya Sharma", **fields})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def _upload(
    api: httpx2.AsyncClient, data: bytes = PDF, name: str = "priya.pdf"
) -> httpx2.Response:
    return await api.post(RESUMES, files={"file": (name, data, "application/octet-stream")})


async def _suggestions(api: httpx2.AsyncClient, resume_id: str) -> list[dict[str, Any]]:
    response = await api.get(f"{PROFILE}/suggestions", params={"resume_id": resume_id})
    return response.json()  # type: ignore[no-any-return]


def _find(suggestions: list[dict[str, Any]], section: str, **match: Any) -> dict[str, Any]:
    return next(
        s for s in suggestions
        if s["section"] == section
        and all(s["proposed_data"].get(k) == v for k, v in match.items())
    )  # fmt: skip


def _stored_files(settings: Settings) -> list[Path]:
    return [p for p in Path(settings.storage_dir).rglob("*") if p.is_file()]


# --- Upload + extraction ----------------------------------------------------------------


async def test_upload_requires_a_profile(api: httpx2.AsyncClient) -> None:
    assert (await _upload(api)).status_code == 404


@pytest.mark.parametrize(
    ("data", "name"), [(PDF, "cv.pdf"), (DOCX, "cv.docx")], ids=["pdf", "docx"]
)
async def test_upload_extracts_but_does_not_modify_the_profile(
    api: httpx2.AsyncClient, settings: Settings, data: bytes, name: str
) -> None:
    await _profile(api)
    response = await _upload(api, data, name)
    assert response.status_code == 201, response.text
    resume = response.json()
    assert resume["parse_status"] == "parsed"
    assert resume["parser_name"] == "heuristic"
    assert resume["is_primary"] is True
    assert "Multi-Agent Research Assistant" in resume["parsed_text"]
    assert resume["pending_suggestions"] == resume["total_suggestions"] > 0
    assert len(_stored_files(settings)) == 1

    # Nothing reached the master profile: it is all awaiting review.
    profile = (await api.get(PROFILE)).json()
    assert all(profile[k] == [] for k in ("educations", "work_experiences", "projects", "skills"))
    assert profile["contact_email"] is None
    assert profile["pending_suggestions"] == resume["pending_suggestions"]

    suggestions = await _suggestions(api, resume["id"])
    assert {s["source"] for s in suggestions} == {"resume_extraction"}
    sections = {s["section"] for s in suggestions}
    assert sections == {
        "personal_info", "education", "work_experience", "project", "certification",
        "achievement", "coursework", "skill",
    }  # fmt: skip
    project = _find(suggestions, "project", title="Multi-Agent Research Assistant")
    assert project["proposed_data"]["repository_url"] == "https://github.com/priya-dev/mara"
    assert project["proposed_data"]["highlights"][0].startswith("Implemented RAG pipeline")
    assert "Multi-Agent Research Assistant" in project["source_excerpt"]
    personal = _find(suggestions, "personal_info")
    assert "full_name" not in personal["proposed_data"]  # same as the profile's name
    assert personal["proposed_data"]["contact_email"] == "priya.sharma@example.test"


async def test_existing_profile_values_are_never_proposed_for_overwrite(
    api: httpx2.AsyncClient,
) -> None:
    await _profile(api, contact_email="mine@example.test", full_name="P. Sharma")
    resume = (await _upload(api)).json()
    personal = _find(await _suggestions(api, resume["id"]), "personal_info")["proposed_data"]
    assert "contact_email" not in personal  # the candidate's own value wins
    assert personal["full_name"] == "Priya Sharma"  # differing name is only proposed


@pytest.mark.parametrize(
    ("data", "name", "message"),
    [
        (b"just some text", "cv.txt", "Unsupported file"),
        (PDF, "cv.docx", "content is PDF"),
        (b"", "cv.pdf", "empty"),
    ],
    ids=["text-file", "mislabelled", "empty"],
)
async def test_invalid_uploads_are_rejected_and_nothing_is_stored(
    api: httpx2.AsyncClient, db: AsyncSession, settings: Settings,
    data: bytes, name: str, message: str,
) -> None:  # fmt: skip
    await _profile(api)
    response = await _upload(api, data, name)
    assert response.status_code == 422
    assert message in response.text
    assert await db.scalar(select(func.count()).select_from(Resume)) == 0
    assert _stored_files(settings) == []


async def test_oversized_upload_is_rejected(db: AsyncSession, user: User, tmp_path: Path) -> None:
    small = Settings(
        _env_file=None, app_env="test", storage_dir=str(tmp_path), max_resume_bytes=500
    )
    async with client_for(db, user, settings=small) as api:
        await _profile(api)
        response = await _upload(api)
    assert response.status_code == 422
    assert "larger than" in response.text


async def test_duplicate_upload_conflicts(api: httpx2.AsyncClient) -> None:
    await _profile(api)
    assert (await _upload(api)).status_code == 201
    assert (await _upload(api, name="copy.pdf")).status_code == 409


async def test_unreadable_pdf_is_recorded_as_failed(api: httpx2.AsyncClient) -> None:
    await _profile(api)
    response = await _upload(api, make_pdf([""]), "scan.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["parse_status"] == "failed"
    assert "Scanned or image-only" in body["parse_error"]
    assert body["total_suggestions"] == 0


# --- Review: accept / edit / reject -----------------------------------------------------


async def test_accepting_creates_the_item_and_one_evidence_row_per_claim(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    project = _find(
        await _suggestions(api, resume["id"]), "project", title="Multi-Agent Research Assistant"
    )

    accepted = await api.post(f"{PROFILE}/suggestions/{project['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted_data"] == project["proposed_data"]

    [item] = (await api.get(PROFILE)).json()["projects"]
    assert item["title"] == "Multi-Agent Research Assistant"
    assert [e["content"] for e in item["evidence"]] == project["proposed_data"]["highlights"]
    assert {e["origin"] for e in item["evidence"]} == {"resume_extracted"}
    assert all(e["confirmed_at"] for e in item["evidence"])
    rows = (await db.scalars(select(CandidateEvidence))).all()
    assert {str(r.source_resume_id) for r in rows} == {resume["id"]}


async def test_edit_then_accept_stores_what_the_user_confirmed(
    api: httpx2.AsyncClient,
) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    cert = _find(await _suggestions(api, resume["id"]), "certification",
                 name="AWS Certified Cloud Practitioner")  # fmt: skip
    edited = {**cert["proposed_data"], "name": "AWS Certified Cloud Practitioner (CLF-C02)"}

    response = await api.post(
        f"{PROFILE}/suggestions/{cert['id']}/accept", json={"proposed_data": edited}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposed_data"]["name"] == "AWS Certified Cloud Practitioner"  # AI's version
    assert body["accepted_data"]["name"] == "AWS Certified Cloud Practitioner (CLF-C02)"

    [item] = (await api.get(PROFILE)).json()["certifications"]
    assert item["name"] == "AWS Certified Cloud Practitioner (CLF-C02)"
    # No bullet points: the verbatim source line is the evidence.
    assert [e["content"] for e in item["evidence"]] == [cert["source_excerpt"]]


async def test_invalid_edit_is_rejected_and_nothing_changes(api: httpx2.AsyncClient) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    job = _find(
        await _suggestions(api, resume["id"]), "work_experience", title="Machine Learning Intern"
    )
    bad = {**job["proposed_data"], "end_date": "2020-01-01"}  # before start_date
    for payload in (bad, {**job["proposed_data"], "origin": "user_entered"}):
        response = await api.post(
            f"{PROFILE}/suggestions/{job['id']}/accept", json={"proposed_data": payload}
        )
        assert response.status_code == 422
    assert (await api.get(PROFILE)).json()["work_experiences"] == []
    assert _find(await _suggestions(api, resume["id"]), "work_experience",
                 title="Machine Learning Intern")["status"] == "pending"  # fmt: skip


async def test_rejecting_leaves_the_profile_untouched(api: httpx2.AsyncClient) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    achievement = _find(await _suggestions(api, resume["id"]), "achievement",
                        title="Winner, Smart India Hackathon 2023")  # fmt: skip
    response = await api.post(f"{PROFILE}/suggestions/{achievement['id']}/reject")
    assert response.json()["status"] == "rejected"
    assert (await api.get(PROFILE)).json()["achievements"] == []


async def test_accepted_skill_is_backed_by_its_source_line(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    suggestions = await _suggestions(api, resume["id"])
    for name in ("Python", "Java"):
        skill = _find(suggestions, "skill", name=name)
        assert (await api.post(f"{PROFILE}/suggestions/{skill['id']}/accept")).status_code == 200

    profile = (await api.get(PROFILE)).json()
    assert sorted(s["name"] for s in profile["skills"]) == ["Java", "Python"]
    [evidence] = profile["evidence"]  # one shared source line for both skills
    assert evidence["content"] == "Languages: Python, Java, C++, SQL"
    assert evidence["origin"] == "resume_extracted"
    links = await db.scalar(select(func.count()).select_from(candidate_evidence_skills))
    assert links == 2


async def test_accepting_personal_info_fills_contact_fields(api: httpx2.AsyncClient) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    personal = _find(await _suggestions(api, resume["id"]), "personal_info")
    await api.post(f"{PROFILE}/suggestions/{personal['id']}/accept")
    profile = (await api.get(PROFILE)).json()
    assert profile["contact_email"] == "priya.sharma@example.test"
    assert profile["linkedin_url"] == "https://linkedin.com/in/priya-sharma-dev"
    assert profile["location"] == "Hyderabad, India"


async def test_reupload_does_not_resuggest_known_information(api: httpx2.AsyncClient) -> None:
    await _profile(api)
    first = (await _upload(api)).json()
    suggestions = await _suggestions(api, first["id"])
    project = _find(suggestions, "project", title="Expense Tracker App")
    await api.post(f"{PROFILE}/suggestions/{project['id']}/accept")
    # Edited on acceptance (so the profile's copy differs from the extracted one) ...
    cert = _find(suggestions, "certification", name="AWS Certified Cloud Practitioner")
    edited = {**cert["proposed_data"], "name": "AWS Cloud Practitioner"}
    await api.post(f"{PROFILE}/suggestions/{cert['id']}/accept", json={"proposed_data": edited})
    # ... and rejected: neither may come back.
    achievement = _find(suggestions, "achievement", title="Winner, Smart India Hackathon 2023")
    await api.post(f"{PROFILE}/suggestions/{achievement['id']}/reject")
    skill = _find(suggestions, "skill", name="SQL")
    await api.post(f"{PROFILE}/suggestions/{skill['id']}/reject")

    second = (await _upload(api, make_docx(SAMPLE_LINES), "priya.docx")).json()
    assert second["parse_status"] == "parsed"
    assert second["total_suggestions"] == 0  # everything is already known or pending


# --- Resume management ------------------------------------------------------------------


async def test_deleting_a_resume_removes_pending_suggestions_but_keeps_accepted_data(
    api: httpx2.AsyncClient, db: AsyncSession, settings: Settings
) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    project = _find(await _suggestions(api, resume["id"]), "project", title="Expense Tracker App")
    await api.post(f"{PROFILE}/suggestions/{project['id']}/accept")

    assert (await api.delete(f"{RESUMES}/{resume['id']}")).status_code == 204
    assert (await api.get(RESUMES)).json() == []
    assert _stored_files(settings) == []
    profile = (await api.get(PROFILE)).json()
    assert profile["pending_suggestions"] == 0
    [item] = profile["projects"]  # accepted information stays
    assert item["evidence"][0]["origin"] == "resume_extracted"
    evidence = await db.get(CandidateEvidence, item["evidence"][0]["id"])
    assert evidence is not None and evidence.source_resume_id is None
    remaining = await db.scalar(select(func.count()).select_from(ProfileSuggestion))
    assert remaining == 1  # the accepted suggestion is kept as history


async def test_resumes_are_private(
    api: httpx2.AsyncClient, db: AsyncSession, settings: Settings
) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    other = await make_user(db, "other@example.test")
    async with client_for(db, other, settings=settings) as other_api:
        await _profile(other_api)
        assert (await other_api.get(f"{RESUMES}/{resume['id']}")).status_code == 404
        assert (await other_api.delete(f"{RESUMES}/{resume['id']}")).status_code == 404
        assert (await other_api.get(RESUMES)).json() == []
    assert len(_stored_files(settings)) == 1


# --- LLM parser path --------------------------------------------------------------------


class FailingProvider(FakeProvider):
    async def complete_json(self, **_: Any) -> LLMJsonResult:
        raise LLMError("The AI provider is rate limiting requests.")


async def test_llm_parser_results_are_grounded_and_logged(
    db: AsyncSession, user: User, settings: Settings
) -> None:
    real_claim = "Deployed the model with FastAPI and Docker on AWS ECS."
    job = {**LLM_OUTPUT["work_experience"][0]}
    job["highlights"] = [real_claim, "Promoted to team lead after two weeks."]  # invented
    invented = {**LLM_OUTPUT, "work_experience": [job]}
    provider = FakeProvider(invented)
    async with client_for(db, user, settings=settings,
                          overrides={get_resume_llm: lambda: provider}) as api:  # fmt: skip
        await _profile(api)
        resume = (await _upload(api)).json()
        suggestions = await _suggestions(api, resume["id"])

    assert resume["parser_name"] == "llm:fake-model"
    job = _find(suggestions, "work_experience", title="Machine Learning Intern")
    assert job["proposed_data"]["highlights"] == [
        "Deployed the model with FastAPI and Docker on AWS ECS."
    ]  # the invented claim never reached the review queue
    assert any("discarded" in w for w in resume["parse_warnings"])
    log = await db.scalar(select(AIExecutionLog))
    assert log is not None and log.status == AIExecutionStatus.SUCCESS
    assert (log.input_tokens, log.output_tokens) == (100, 50)
    assert job["proposed_data"] and all(s["source"] == "resume_extraction" for s in suggestions)


async def test_llm_failure_falls_back_to_the_rule_based_parser(
    db: AsyncSession, user: User, settings: Settings
) -> None:
    provider = FailingProvider({})
    async with client_for(db, user, settings=settings,
                          overrides={get_resume_llm: lambda: provider}) as api:  # fmt: skip
        await _profile(api)
        resume = (await _upload(api)).json()
    assert resume["parser_name"] == "heuristic"
    assert resume["total_suggestions"] > 0
    assert any("AI parsing failed" in w for w in resume["parse_warnings"])
    log = await db.scalar(select(AIExecutionLog))
    assert log is not None and log.status == AIExecutionStatus.ERROR


async def test_evidence_origin_is_never_user_entered_for_resume_data(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await _profile(api)
    resume = (await _upload(api)).json()
    for s in await _suggestions(api, resume["id"]):
        if s["section"] in ("project", "work_experience"):
            await api.post(f"{PROFILE}/suggestions/{s['id']}/accept")
    origins = set(await db.scalars(select(CandidateEvidence.origin)))
    assert origins == {EvidenceOrigin.RESUME_EXTRACTED}
