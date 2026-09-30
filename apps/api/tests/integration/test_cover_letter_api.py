"""Cover letters end to end: grounded generation, the verification engine, regeneration
of failed sentences, editing, downloads, versions and evidence protection."""

import io
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from docx import Document
from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIExecutionLog, AIExecutionStatus
from app.ai.provider import LLMError, LLMJsonResult
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.cover_letters import get_letter_llm
from app.api.routes.matching import get_match_llm
from app.api.routes.verification import get_verification_llm
from app.core.config import Settings
from app.documents.models import CoverLetter, GeneratedClaim, TailoredResume
from app.users.models import User

from .conftest import client_for, make_user
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, PROFILE, build_job, post
from .test_tailored_resume_api import EVIDENCE, build_profile

pytestmark = pytest.mark.anyio
LETTERS = "/api/v1/cover-letters"
# Things the candidate never did or said. None may appear in a final letter.
HALLUCINATIONS = [
    "passionate", "team player", "Innovation Award", "Google", "60%", "10 years", "senior",
    "search ranking",
]  # fmt: skip


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


def _client(
    db: AsyncSession, user: User, embedder: SpyEmbedder, llm: Any = None, **settings: Any
) -> httpx2.AsyncClient:
    config = Settings(_env_file=None, app_env="test", **settings)
    overrides = {
        get_embedder: lambda: embedder,
        get_letter_llm: lambda: llm,
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


async def write(api: httpx2.AsyncClient, job_id: str) -> dict[str, Any]:
    response = await api.post(f"{JOBS}/{job_id}/cover-letters")
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def sentences(letter: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for p in letter["content"]["paragraphs"] for s in p["sentences"]]


def full_text(letter: dict[str, Any]) -> str:
    c = letter["content"]
    body = " ".join(s["text"] for s in sentences(letter))
    return f"{c['greeting']} {body} {c['closing']}"


def assert_grounded(letter: dict[str, Any], ids: dict[str, Any]) -> None:
    own = {v for k, v in ids.items() if k != "records"}
    report = {c["claim_text"]: c for c in letter["report"]["claims"]}
    for sentence in sentences(letter):
        result = report[sentence["text"]]
        assert result["verification_status"] == "supported", result
        assert set(sentence["evidence_ids"]) <= own
        if not result["reason"].startswith("Not a factual claim"):
            assert sentence["evidence_ids"], f"uncited factual sentence: {sentence['text']}"
    text = full_text(letter).lower()
    for invented in HALLUCINATIONS:
        assert invented.lower() not in text, f"hallucination reached the letter: {invented}"


# --- Rule-based letters -------------------------------------------------------------------


async def test_a_concise_letter_grounded_in_verified_evidence(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    ids = await build_profile(api)
    letter = await write(api, await build_job(api))
    assert_grounded(letter, ids)
    assert letter["status"] == "verified" and letter["generator"] == "rules"
    assert letter["report"]["outcome"] == "approved"
    assert letter["report"]["document_type"] == "cover_letter"
    assert letter["report"]["trigger"] == "generation"

    content = letter["content"]
    assert (content["job_title"], content["company_name"]) == ("ML Engineer", "Northwind")
    assert content["greeting"] == "Dear Northwind Hiring Team,"
    assert content["signature"]["full_name"] == "Test Candidate"
    assert sentences(letter)[0]["text"] == (
        "I am writing to apply for the ML Engineer position at Northwind."
    )
    texts = [s["text"] for s in sentences(letter)]
    assert (
        "As a Machine Learning Intern at Acme Analytics, I deployed ML models with Docker on AWS."
        in texts
    )
    assert any(t.startswith("I have worked with") for t in texts)
    assert 40 <= letter["word_count"] <= 300 and len(content["paragraphs"]) <= 5
    # Requirements the evidence doesn't meet are left out, and the candidate is told.
    assert "Kubernetes" not in full_text(letter)
    assert any("Kubernetes" in n for n in letter["notes"])
    # Stored separately, with every claim verified and linked.
    assert await db.scalar(select(func.count()).select_from(CoverLetter)) == 1
    assert await db.scalar(select(func.count()).select_from(TailoredResume)) == 0
    claims = list(await db.scalars(select(GeneratedClaim)))
    assert len(claims) == len(sentences(letter))
    assert all(c.status == "verified" and c.cover_letter_id for c in claims)


async def test_an_empty_profile_gets_only_non_factual_sentences(api: httpx2.AsyncClient) -> None:
    await post(api, PROFILE, {"full_name": "New Candidate"})
    letter = await write(api, await build_job(api))
    assert letter["status"] == "verified"
    assert all(not s["evidence_ids"] for s in sentences(letter))
    assert any("doesn't describe work or projects" in n for n in letter["notes"])


# --- A hallucinating model -----------------------------------------------------------------


class Writer:
    """Writes a letter full of inventions, then 'fixes' some of them when asked."""

    name, model = "fake", "fake-writer"

    def __init__(self, foreign: str | None = None, fail: bool = False) -> None:
        self.foreign, self.fail = foreign, fail
        self.calls: list[str] = []

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
    ) -> LLMJsonResult:
        if self.fail:
            raise LLMError("the model is unavailable")
        if "<sentences>" in prompt:
            self.calls.append("revise")
            return self._revise(prompt)
        self.calls.append("write")
        found = re.search(r"<job_and_candidate>\n(.*)\n</job_and_candidate>", prompt, re.S)
        assert found is not None
        data = json.loads(found.group(1))
        ev = {e["text"]: e["evidence_id"]
              for r in data["candidate"]["jobs"] + data["candidate"]["projects"]
              for e in r["evidence"]}  # fmt: skip
        docker, latency = ev[EVIDENCE["docker"][1]], ev[EVIDENCE["latency"][1]]
        letter = {
            "greeting": "Dear Northwind, from a senior engineer with 10 years of experience,",
            "paragraphs": [
                {"sentences": [
                    {"text": "I am writing to apply for the ML Engineer position at Northwind.",
                     "evidence_ids": []},
                    {"text": "I am a passionate, detail-oriented team player.",
                     "evidence_ids": []},
                ]},
                {"sentences": [
                    {"text": "At Acme Analytics, I deployed ML models with Docker on AWS.",
                     "evidence_ids": [docker]},
                    {"text": "At Acme Analytics, I reduced model inference latency by 60% "
                             "using ONNX.", "evidence_ids": [latency]},
                    {"text": "I won the Acme Innovation Award for my deployment work.",
                     "evidence_ids": [docker]},
                    {"text": "I previously worked at Google on search ranking.",
                     "evidence_ids": [self.foreign] if self.foreign else []},
                ]},
                {"sentences": [
                    {"text": "Thank you for your time and consideration.", "evidence_ids": []},
                ]},
            ],
            "closing": "Sincerely,",
        }  # fmt: skip
        return LLMJsonResult(letter, "fake", "fake-writer", 700, 300, 20)

    def _revise(self, prompt: str) -> LLMJsonResult:
        found = re.search(r"<sentences>\n(.*)\n</sentences>", prompt, re.S)
        assert found is not None
        revisions = []
        for item in json.loads(found.group(1)):
            text, ids = "", [e["evidence_id"] for e in item["evidence"]]
            if "60%" in item["sentence"]:  # fixed: the evidence's own number
                text = "At Acme Analytics, I reduced model inference latency by 35% using ONNX."
            elif "Award" in item["sentence"]:  # still invented: must be removed
                text = "I received an innovation award for my deployment work."
            revisions.append({"sentence_id": item["sentence_id"], "text": text,
                              "evidence_ids": ids[:1]})  # fmt: skip
        return LLMJsonResult({"revisions": revisions}, "fake", "fake-writer", 300, 80, 10)


async def test_failed_sentences_are_regenerated_or_removed(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    other = await make_user(db, "someone-else@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Other Candidate"})
        body = {"source_type": "profile", "content": "Worked at Google on search ranking."}
        foreign = await post(stranger, f"{PROFILE}/evidence", body)

    writer = Writer(foreign["id"])
    async with _client(db, user, embedder, writer) as api:
        ids = await build_profile(api)
        letter = await write(api, await build_job(api))
    assert writer.calls == ["write", "revise"]
    assert letter["generator"] == "llm:fake-writer"
    assert_grounded(letter, ids)
    assert foreign["id"] not in json.dumps(letter)
    assert letter["status"] == "verified" and letter["report"]["outcome"] == "approved"

    texts = [s["text"] for s in sentences(letter)]
    assert "At Acme Analytics, I deployed ML models with Docker on AWS." in texts  # faithful
    assert "At Acme Analytics, I reduced model inference latency by 35% using ONNX." in texts
    assert letter["content"]["greeting"] == "Dear Northwind Hiring Team,"  # plain greeting

    audit = {a["original_text"]: a for a in letter["changes"]["audit"]}
    expected = {
        "Dear Northwind, from a senior engineer with 10 years of experience,": (
            "regenerated", "Dear Northwind Hiring Team,"),
        "I am a passionate, detail-oriented team player.": ("removed", "passionate"),
        "At Acme Analytics, I reduced model inference latency by 60% using ONNX.": (
            "regenerated", "says 35%, not 60%"),
        "I won the Acme Innovation Award for my deployment work.": (
            "removed", "regenerated sentence also failed"),
        "I previously worked at Google on search ranking.": ("removed", "Google"),
    }  # fmt: skip
    for original, (outcome, detail) in expected.items():
        assert original in audit, f"not audited: {original}"
        item = audit[original]
        assert item["outcome"] == outcome, item
        assert detail.lower() in f"{item['reason']} {item['final_text']}".lower(), item
    assert audit["At Acme Analytics, I reduced model inference latency by 60% using ONNX."][
        "verdict"] == "contradicted"  # fmt: skip
    assert letter["changes"]["regenerated"] == 2 and letter["changes"]["removed"] == 3

    logs = list(await db.scalars(select(AIExecutionLog).order_by(AIExecutionLog.created_at)))
    assert [log.operation for log in logs] == ["cover_letter_generation"] * 2
    assert all(log.status == AIExecutionStatus.SUCCESS for log in logs)
    assert {log.prompt_version for log in logs} == {"cover-letter-v1", "cover-letter-v1-revise"}


async def test_model_failure_falls_back_to_the_rule_letter(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    async with _client(db, user, embedder, Writer(fail=True)) as api:
        ids = await build_profile(api)
        letter = await write(api, await build_job(api))
    assert letter["generator"] == "rules"
    assert_grounded(letter, ids)
    assert any("AI writing failed" in n for n in letter["notes"])
    log = await db.scalar(select(AIExecutionLog))
    assert log is not None and log.status == AIExecutionStatus.ERROR


async def test_rules_setting_never_calls_the_model(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    writer = Writer()
    async with _client(db, user, embedder, writer, cover_letter_generator="rules") as api:
        await build_profile(api)
        letter = await write(api, await build_job(api))
    assert writer.calls == [] and letter["generator"] == "rules"


# --- Edit and save -----------------------------------------------------------------------


def paragraphs(letter: dict[str, Any]) -> list[str]:
    return [" ".join(s["text"] for s in p["sentences"]) for p in letter["content"]["paragraphs"]]


async def save(api: httpx2.AsyncClient, letter: dict[str, Any], **changes: Any) -> httpx2.Response:
    body = {"greeting": letter["content"]["greeting"], "paragraphs": paragraphs(letter),
            "closing": letter["content"]["closing"], **changes}  # fmt: skip
    return await api.put(f"{LETTERS}/{letter['id']}", json=body)


async def test_supported_edits_are_saved(api: httpx2.AsyncClient) -> None:
    ids = await build_profile(api)
    letter = await write(api, await build_job(api))
    edited = paragraphs(letter)
    edited[0] += " I am excited about the ML Engineer role at Northwind."
    # A true statement typed without citations: the engine finds the evidence for it.
    edited.insert(1, "I reduced model inference latency by 35% using ONNX.")
    response = await save(api, letter, paragraphs=edited, closing="Kind regards,")
    assert response.status_code == 200, response.text
    saved = response.json()
    assert_grounded(saved, ids)
    assert saved["content"]["closing"] == "Kind regards,"
    [typed] = [s for s in sentences(saved) if s["text"].startswith("I reduced")]
    assert typed["evidence_ids"] == [ids["latency"]]
    assert any("now cites your evidence" in n for n in saved["notes"])
    assert saved["report"]["trigger"] == "edit" and saved["status"] == "verified"


@pytest.mark.parametrize(
    ("change", "key", "detail"),
    [
        ({"paragraphs": ["I reduced model inference latency by 80% using ONNX."]},
         "paragraphs[0]", "35%, not 80%"),
        ({"paragraphs": ["I am a passionate and hard-working team player."]},
         "paragraphs[0]", "generic quality"),
        ({"paragraphs": ["I previously worked at Google on search ranking."]},
         "paragraphs[0]", "Unsupported"),
        ({"paragraphs": ["As a Senior Engineer at Acme Analytics, I deployed ML models."]},
         "paragraphs[0]", "Contradicted"),
        ({"greeting": "Dear Northwind, from an award-winning engineer,"},
         "greeting", "Unsupported"),
    ],
)  # fmt: skip
async def test_unsupported_edits_are_rejected_and_nothing_is_saved(
    api: httpx2.AsyncClient, change: dict[str, Any], key: str, detail: str
) -> None:
    await build_profile(api)
    letter = await write(api, await build_job(api))
    response = await save(api, letter, **change)
    assert response.status_code == 422, response.text
    errors = {d["loc"][1]: d["msg"] for d in response.json()["detail"]}
    assert key in errors and detail.lower() in errors[key].lower(), errors
    after = (await api.get(f"{LETTERS}/{letter['id']}")).json()
    assert after["content"] == letter["content"]


async def test_regenerating_replaces_the_draft(api: httpx2.AsyncClient, db: AsyncSession) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    first = await write(api, job_id)
    second = await write(api, job_id)
    assert second["version"] == 2
    assert (await api.get(f"{LETTERS}/{first['id']}")).status_code == 404
    latest = await api.get(f"{JOBS}/{job_id}/cover-letters/latest")
    assert latest.json()["id"] == second["id"]
    assert await db.scalar(select(func.count()).select_from(CoverLetter)) == 1


async def test_latest_is_404_before_generation(api: httpx2.AsyncClient) -> None:
    await post(api, PROFILE, {"full_name": "Test Candidate"})
    job_id = await build_job(api)
    assert (await api.get(f"{JOBS}/{job_id}/cover-letters/latest")).status_code == 404


# --- Download, verification, protection ----------------------------------------------------


async def test_downloads_contain_exactly_the_letter(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    letter = await write(api, await build_job(api))
    base = f"{LETTERS}/{letter['id']}/download"
    pdf = await api.get(base, params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"] == (
        'attachment; filename="Test-Candidate-cover-letter-v1.pdf"'
    )
    pdf_text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    docx = await api.get(base, params={"format": "docx"})
    docx_text = "\n".join(p.text for p in Document(io.BytesIO(docx.content)).paragraphs)
    flat_pdf = re.sub(r"\s+", " ", pdf_text)
    for text in ("Test Candidate", "Re: ML Engineer, Northwind", "Dear Northwind Hiring Team,",
                 *[s["text"] for s in sentences(letter)], "Sincerely,"):  # fmt: skip
        assert text in docx_text, text
        assert re.sub(r"\s+", " ", text) in flat_pdf, text


async def test_reverification_reports_profile_changes_without_editing_the_letter(
    api: httpx2.AsyncClient,
) -> None:
    await build_profile(api)
    letter = await write(api, await build_job(api))
    response = await api.patch(PROFILE, json={"full_name": "Test Candidate-Rao"})
    assert response.status_code == 200, response.text
    checked = (await api.post(f"{LETTERS}/{letter['id']}/verify")).json()
    assert checked["status"] == "verification_failed"
    assert checked["content"] == letter["content"]
    [signature] = [c for c in checked["report"]["claims"] if c["section"] == "signature"]
    assert signature["verification_status"] == "contradicted"
    history = (await api.get(f"{LETTERS}/{letter['id']}/verification-reports")).json()
    assert [r["trigger"] for r in history] == ["manual", "generation"]


async def test_cited_evidence_and_the_job_are_protected_until_the_letter_is_deleted(
    api: httpx2.AsyncClient,
) -> None:
    ids = await build_profile(api)
    job_id = await build_job(api)
    letter = await write(api, job_id)
    assert (await api.delete(f"{PROFILE}/evidence/{ids['docker']}")).status_code == 409
    assert (await api.delete(f"{JOBS}/{job_id}")).status_code == 409
    assert (await api.delete(f"{LETTERS}/{letter['id']}")).status_code == 204
    assert (await api.delete(f"{PROFILE}/evidence/{ids['docker']}")).status_code == 204
    assert (await api.delete(f"{JOBS}/{job_id}")).status_code == 204


async def test_cover_letters_are_private(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    async with _client(db, user, embedder) as api:
        await build_profile(api)
        job_id = await build_job(api)
        letter = await write(api, job_id)
    other = await make_user(db, "other@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Other"})
        body = {"greeting": "Dear Team,", "paragraphs": ["Thank you."], "closing": "Sincerely,"}
        for response in (
            await stranger.get(f"{LETTERS}/{letter['id']}"),
            await stranger.put(f"{LETTERS}/{letter['id']}", json=body),
            await stranger.post(f"{LETTERS}/{letter['id']}/verify"),
            await stranger.get(f"{LETTERS}/{letter['id']}/download"),
            await stranger.get(f"{LETTERS}/{letter['id']}/verification-reports"),
            await stranger.delete(f"{LETTERS}/{letter['id']}"),
            await stranger.post(f"{JOBS}/{job_id}/cover-letters"),
        ):
            assert response.status_code == 404
