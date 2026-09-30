"""Application answers end to end: understanding, retrieval, grounded answers, verification,
evidence used, editing, regenerating and approving."""

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIExecutionLog, AIExecutionStatus
from app.ai.provider import LLMJsonResult
from app.api.routes.application_answers import get_answer_llm
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.matching import get_match_llm
from app.api.routes.verification import get_verification_llm
from app.core.config import Settings
from app.documents.models import ApplicationAnswer, GeneratedClaim
from app.users.models import User

from .conftest import client_for, make_user
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, PROFILE, build_job, post
from .test_tailored_resume_api import EVIDENCE, build_profile

pytestmark = pytest.mark.anyio
ANSWERS = "/api/v1/application-answers"
SPEC_QUESTIONS = [
    "Why are you interested in this role?",
    "Describe a relevant project.",
    "Why should we hire you?",
    "Describe your experience with Python.",
]


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


def _client(
    db: AsyncSession, user: User, embedder: SpyEmbedder, llm: Any = None, **settings: Any
) -> httpx2.AsyncClient:
    config = Settings(_env_file=None, app_env="test", **settings)
    overrides = {
        get_embedder: lambda: embedder,
        get_answer_llm: lambda: llm,
        get_match_llm: lambda: None,
        get_verification_llm: lambda: None,
    }
    return client_for(db, user, settings=config, overrides=overrides)


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def ask(
    api: httpx2.AsyncClient, job_id: str, questions: list[str], **extra: Any
) -> list[dict[str, Any]]:
    response = await api.post(
        f"{JOBS}/{job_id}/application-answers", json={"questions": questions, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def assert_grounded(answer: dict[str, Any], ids: dict[str, Any]) -> None:
    """Every sentence is supported; factual ones cite the candidate's own evidence, and the
    evidence shown is exactly the evidence cited."""
    own = {v for k, v in ids.items() if k != "records"}
    results = {r["claim_text"]: r for r in answer["report"]["claims"]}
    cited: list[str] = []
    for sentence in answer["sentences"]:
        result = results[sentence["text"]]
        assert result["verification_status"] == "supported", result
        assert set(sentence["evidence_ids"]) <= own
        if not result["reason"].startswith("Not a factual claim"):
            assert sentence["evidence_ids"], sentence
        cited += sentence["evidence_ids"]
    assert [e["evidence_id"] for e in answer["evidence_used"]] == list(dict.fromkeys(cited))


# --- The four questions -------------------------------------------------------------------


async def test_the_four_example_questions(api: httpx2.AsyncClient, db: AsyncSession) -> None:
    ids = await build_profile(api)
    answers = await ask(api, await build_job(api), SPEC_QUESTIONS)
    by_question = {a["question"]: a for a in answers}
    assert [a["question"] for a in answers] == SPEC_QUESTIONS
    assert [a["position"] for a in answers] == [1, 2, 3, 4]
    for answer in answers:
        assert answer["status"] == "verified", answer
        assert answer["text"] and answer["evidence_used"]
        assert_grounded(answer, ids)
        assert answer["report"]["document_type"] == "application_answer"
        assert answer["report"]["outcome"] == "approved"

    motivation = by_question["Why are you interested in this role?"]
    assert motivation["question_type"] == "motivation"
    assert motivation["sentences"][0]["text"] == (
        "I am interested in the ML Engineer role at Northwind."
    )

    project = by_question["Describe a relevant project."]
    assert project["question_type"] == "project"
    assert project["text"].startswith("In my Multi-Agent Research Assistant project, I ")
    assert {e["record_label"] for e in project["evidence_used"]} == {
        "Multi-Agent Research Assistant"
    }

    fit = by_question["Why should we hire you?"]
    assert fit["question_type"] == "fit" and len(fit["sentences"]) >= 2

    python = by_question["Describe your experience with Python."]
    assert (python["question_type"], python["focus"]) == ("skill", "Python")
    assert python["understanding"] == "Asks about your experience with Python."
    assert [e["evidence_id"] for e in python["evidence_used"]] == [ids["python"]]
    assert "Python" in python["text"]

    assert await db.scalar(select(func.count()).select_from(ApplicationAnswer)) == 4
    claims = list(await db.scalars(select(GeneratedClaim)))
    assert claims and all(c.application_answer_id and c.status == "verified" for c in claims)


async def test_a_skill_without_evidence_is_not_answered(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    [answer] = await ask(api, await build_job(api), ["What is your experience with Kubernetes?"])
    assert answer["focus"] == "Kubernetes"
    assert answer["status"] == "draft" and answer["text"] == "" and answer["evidence_used"] == []
    assert "doesn't show experience with Kubernetes" in answer["notes"][0]
    response = await api.post(f"{ANSWERS}/{answer['id']}/approve")
    assert response.status_code == 409


async def test_word_limits_are_respected(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    [answer] = await ask(api, await build_job(api), ["Why should we hire you?"], max_words=25)
    assert answer["max_words"] == 25
    assert answer["word_count"] <= 25 or len(answer["sentences"]) == 1


async def test_questions_are_validated(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    url = f"{JOBS}/{job_id}/application-answers"
    bodies: list[dict[str, Any]] = [
        {"questions": []},
        {"questions": ["Hi"]},
        {"questions": ["Why?"] * 11},
        {"questions": ["Why should we hire you?"], "max_words": 5},
    ]
    for body in bodies:
        assert (await api.post(url, json=body)).status_code == 422, body


# --- A hallucinating model -----------------------------------------------------------------


class Writer:
    """Answers with inventions, then 'fixes' one of them when asked."""

    name, model = "fake", "fake-writer"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
    ) -> LLMJsonResult:
        if "<sentences>" in prompt:
            self.calls.append("revise")
            found = re.search(r"<sentences>\n(.*)\n</sentences>", prompt, re.S)
            assert found is not None
            revisions = []
            for item in json.loads(found.group(1)):
                fixed = "70%" in item["sentence"]
                revisions.append(
                    {
                        "sentence_id": item["sentence_id"],
                        "text": "I reduced model inference latency by 35% using ONNX."
                        if fixed
                        else "",
                        "evidence_ids": [e["evidence_id"] for e in item["evidence"]][:1],
                    }
                )
            return LLMJsonResult({"revisions": revisions}, "fake", "fake-writer", 200, 50, 5)
        self.calls.append("write")
        found = re.search(r"<question_and_evidence>\n(.*)\n</question_and_evidence>", prompt, re.S)
        assert found is not None
        evidence = {e["text"]: e["evidence_id"] for e in json.loads(found.group(1))["evidence"]}
        docker = evidence.get(EVIDENCE["docker"][1])
        latency = evidence.get(EVIDENCE["latency"][1])
        sentences = [
            {"text": "I deployed ML models with Docker on AWS.", "evidence_ids": [docker]},
            {
                "text": "I reduced model inference latency by 70% using ONNX.",
                "evidence_ids": [latency],
            },
            {
                "text": "I am a passionate team player with a proven track record.",
                "evidence_ids": [],
            },
            {"text": "I led the ML platform team at Google for 5 years.", "evidence_ids": [docker]},
        ]
        return LLMJsonResult({"sentences": sentences}, "fake", "fake-writer", 500, 150, 12)


async def test_invented_experience_is_regenerated_or_removed(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    writer = Writer()
    async with _client(db, user, embedder, writer) as api:
        ids = await build_profile(api)
        [answer] = await ask(api, await build_job(api), ["Why should we hire you?"])
    assert writer.calls == ["write", "revise"]
    assert answer["generator"] == "llm:fake-writer" and answer["status"] == "verified"
    assert_grounded(answer, ids)
    assert [s["text"] for s in answer["sentences"]] == [
        "I deployed ML models with Docker on AWS.",
        "I reduced model inference latency by 35% using ONNX.",
    ]
    for invented in ("70%", "passionate", "track record", "Google", "5 years", "led"):
        assert invented not in answer["text"]
    audit = {a["original_text"]: a for a in answer["changes"]["audit"]}
    assert audit["I reduced model inference latency by 70% using ONNX."]["outcome"] == (
        "regenerated"
    )
    assert audit["I reduced model inference latency by 70% using ONNX."]["verdict"] == (
        "contradicted"
    )
    assert (
        "generic quality"
        in (audit["I am a passionate team player with a proven track record."]["reason"])
    )
    assert audit["I led the ML platform team at Google for 5 years."]["outcome"] == "removed"
    logs = list(await db.scalars(select(AIExecutionLog)))
    assert {log.operation for log in logs} == {"application_answer"}
    assert all(log.status == AIExecutionStatus.SUCCESS for log in logs)


# --- Edit, regenerate, approve -------------------------------------------------------------


async def test_edit_approve_and_edit_again(api: httpx2.AsyncClient) -> None:
    ids = await build_profile(api)
    [answer] = await ask(api, await build_job(api), ["Describe your experience with Python."])
    url = f"{ANSWERS}/{answer['id']}"

    edited = "I built data pipelines in Python and SQL. I would welcome the chance to discuss it."
    response = await api.put(url, json={"answer": edited})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert_grounded(saved, ids)
    assert saved["text"] == edited and saved["status"] == "verified"
    assert saved["report"]["trigger"] == "edit"

    approved = (await api.post(f"{url}/approve")).json()
    assert approved["status"] == "approved" and approved["approved_at"]
    assert approved["report"]["trigger"] == "approval"

    again = (
        await api.put(url, json={"answer": "I built data pipelines in Python and SQL."})
    ).json()
    assert again["status"] == "verified" and again["approved_at"] is None
    assert any("withdrew your approval" in n for n in again["notes"])


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        ("I built data pipelines in Python and SQL for 5 years.", "5"),
        ("I am an expert in Python with extensive experience.", "Unsupported"),
        ("I built data pipelines in Python and SQL at Google.", "Google"),
        ("I reduced model inference latency by 90% using ONNX.", "35%, not 90%"),
    ],
)
async def test_unsupported_edits_are_rejected(
    api: httpx2.AsyncClient, text: str, detail: str
) -> None:
    await build_profile(api)
    [answer] = await ask(api, await build_job(api), ["Describe your experience with Python."])
    response = await api.put(f"{ANSWERS}/{answer['id']}", json={"answer": text})
    assert response.status_code == 422, response.text
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "answer"] and detail in error["msg"], error
    after = (await api.get(f"{ANSWERS}/{answer['id']}")).json()
    assert after["text"] == answer["text"]


async def test_approval_reverifies_against_the_current_profile(api: httpx2.AsyncClient) -> None:
    ids = await build_profile(api)
    [answer] = await ask(api, await build_job(api), ["Why should we hire you?"])
    assert any(s["text"].startswith("As a Machine Learning Intern") for s in answer["sentences"])
    job_id = ids["records"]["work_experience"]
    response = await api.put(
        f"{PROFILE}/work-experiences/{job_id}",
        json={
            "title": "Data Science Intern",
            "company_name": "Acme Analytics",
            "start_date": "2023-05-01",
            "end_date": "2024-05-01",
        },
    )
    assert response.status_code == 200
    response = await api.post(f"{ANSWERS}/{answer['id']}/approve")
    assert response.status_code == 409
    after = (await api.get(f"{ANSWERS}/{answer['id']}")).json()
    assert after["status"] == "verification_failed" and after["approved_at"] is None
    assert after["report"]["trigger"] == "approval" and after["report"]["outcome"] == "rejected"
    assert after["text"] == answer["text"]  # reported, never silently changed

    regenerated = (await api.post(f"{ANSWERS}/{answer['id']}/regenerate")).json()
    assert regenerated["status"] == "verified"
    assert "Data Science Intern" in regenerated["text"]
    assert (await api.post(f"{ANSWERS}/{answer['id']}/approve")).status_code == 200


async def test_answers_are_listed_in_order_and_can_be_added_later(
    api: httpx2.AsyncClient,
) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    await ask(api, job_id, SPEC_QUESTIONS[:2])
    await ask(api, job_id, SPEC_QUESTIONS[2:])
    listed = (await api.get(f"{JOBS}/{job_id}/application-answers")).json()
    assert [a["question"] for a in listed] == SPEC_QUESTIONS
    assert [a["position"] for a in listed] == [1, 2, 3, 4]


# --- Protection and privacy -----------------------------------------------------------------


async def test_cited_evidence_is_protected_and_jobs_take_their_answers_with_them(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    ids = await build_profile(api)
    job_id = await build_job(api)
    [answer] = await ask(api, job_id, ["Describe your experience with Python."])
    assert (await api.delete(f"{PROFILE}/evidence/{ids['python']}")).status_code == 409
    assert (await api.delete(f"{ANSWERS}/{answer['id']}")).status_code == 204
    assert (await api.delete(f"{PROFILE}/evidence/{ids['python']}")).status_code == 204
    await ask(api, job_id, ["Why should we hire you?"])
    assert (await api.delete(f"{JOBS}/{job_id}")).status_code == 204
    assert await db.scalar(select(func.count()).select_from(ApplicationAnswer)) == 0


async def test_answers_are_private(db: AsyncSession, user: User, embedder: SpyEmbedder) -> None:
    async with _client(db, user, embedder) as api:
        await build_profile(api)
        job_id = await build_job(api)
        [answer] = await ask(api, job_id, ["Why should we hire you?"])
    other = await make_user(db, "other@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Other"})
        for response in (
            await stranger.get(f"{ANSWERS}/{answer['id']}"),
            await stranger.put(f"{ANSWERS}/{answer['id']}", json={"answer": "Thank you."}),
            await stranger.post(f"{ANSWERS}/{answer['id']}/approve"),
            await stranger.post(f"{ANSWERS}/{answer['id']}/regenerate"),
            await stranger.delete(f"{ANSWERS}/{answer['id']}"),
            await stranger.get(f"{JOBS}/{job_id}/application-answers"),
            await stranger.post(
                f"{JOBS}/{job_id}/application-answers",
                json={"questions": ["Why should we hire you?"]},
            ),
        ):
            assert response.status_code == 404
