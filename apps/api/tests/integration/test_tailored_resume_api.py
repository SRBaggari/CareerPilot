"""Job-specific resume tailoring, end to end, with a focus on catching hallucinated claims.

Every test checks the same invariant from a different angle: nothing reaches the final
resume unless it is a record fact copied from the profile or a claim backed by the
candidate's own verified evidence.
"""

import copy
import io
import json
import re
import uuid
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
from app.api.routes.matching import get_match_llm
from app.api.routes.tailored_resumes import get_tailor_llm
from app.core.config import Settings
from app.documents.models import (
    ClaimStatus,
    ClaimVerification,
    GeneratedClaim,
    TailoredResume,
    generated_claim_evidence,
)
from app.profiles.models import CandidateEvidence, EvidenceOrigin, EvidenceSourceType, Resume
from app.users.models import User

from .conftest import client_for, make_user
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, PROFILE, build_job, post

pytestmark = pytest.mark.anyio

EVIDENCE = {
    "rag": ("project", "Implemented RAG pipeline in Multi-Agent Research Assistant."),
    "python": ("project", "Built data pipelines in Python and SQL."),
    "docker": ("work_experience", "Deployed ML models with Docker on AWS."),
    "latency": ("work_experience", "Reduced model inference latency by 35% using ONNX."),
    "degree": ("education", "B.Tech in Computer Science, State University."),
}
# Things the candidate never did. None may appear anywhere in a final resume.
HALLUCINATIONS = [
    "Kubernetes",
    "60%",
    "AWS Certified",
    "Google",
    "Spearheaded",
    "millions",
    "Quantum Trading Platform",
    "2019",
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
        get_tailor_llm: lambda: llm,
        get_match_llm: lambda: None,
    }
    return client_for(db, user, settings=config, overrides=overrides)


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def build_profile(api: httpx2.AsyncClient) -> dict[str, Any]:
    await post(api, PROFILE, {"full_name": "Test Candidate", "contact_email": "t@example.test"})
    records = {
        "project": await post(
            api, f"{PROFILE}/projects", {"title": "Multi-Agent Research Assistant"}
        ),
        "work_experience": await post(
            api,
            f"{PROFILE}/work-experiences",
            {
                "title": "Machine Learning Intern",
                "company_name": "Acme Analytics",
                "start_date": "2023-05-01",
                "end_date": "2024-05-01",
            },
        ),
        "education": await post(
            api,
            f"{PROFILE}/educations",
            {
                "institution": "State University",
                "degree": "B.Tech",
                "degree_level": "bachelor",
                "field_of_study": "Computer Science",
                "end_date": "2023-06-01",
            },
        ),
    }
    await post(api, f"{PROFILE}/coursework", {"course_name": "Machine Learning"})
    ids: dict[str, Any] = {"records": {k: v["id"] for k, v in records.items()}}
    for key, (source, content) in EVIDENCE.items():
        created = await post(
            api,
            f"{PROFILE}/evidence",
            {"source_type": source, "subject_id": records[source]["id"], "content": content},
        )
        ids[key] = created["id"]
    for skill in ("Python", "Docker", "Terraform"):  # Terraform: listed, but no evidence
        await post(api, f"{PROFILE}/skills", {"name": skill})
    return ids


async def tailor(api: httpx2.AsyncClient, job_id: str) -> dict[str, Any]:
    response = await api.post(f"{JOBS}/{job_id}/tailored-resumes")
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def resume_text(resume: dict[str, Any]) -> str:
    return json.dumps(resume["content"])


def all_claims(content: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found = [("summary", c) for c in content["summary"]]
    found += [("skills", c) for c in content["skills"]]
    for section in ("experience", "projects"):
        for entry in content[section]:
            found += [(f"{section}:{entry['record_id']}", c) for c in entry["bullets"]]
    return found


def assert_grounded(resume: dict[str, Any], ids: dict[str, Any]) -> None:
    """The core anti-hallucination invariant."""
    own = {v for k, v in ids.items() if k != "records"}
    subject = {ids[k]: EVIDENCE[k][0] for k in EVIDENCE}
    for section, claim in all_claims(resume["content"]):
        assert claim["evidence_ids"], f"uncited claim in {section}: {claim['text']}"
        assert set(claim["evidence_ids"]) <= own, f"foreign evidence in {section}"
        assert claim["claim_id"], "every final claim is stored"
        if ":" in section:  # bullets cite their own record's evidence
            kind, record = section.split(":")
            source = "work_experience" if kind == "experience" else "project"
            assert record == ids["records"][source]
            assert {subject[e] for e in claim["evidence_ids"]} == {source}
    text = resume_text(resume)
    for invented in HALLUCINATIONS:
        assert invented not in text, f"hallucination reached the resume: {invented}"


# --- Rule-based tailoring ---------------------------------------------------------------


async def test_tailored_resume_is_grounded_and_keeps_record_facts(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    ids = await build_profile(api)
    resume = await tailor(api, await build_job(api))
    assert_grounded(resume, ids)
    assert resume["generator"] == "rules" and resume["version"] == 1
    assert resume["status"] == "verified"

    content = resume["content"]
    assert content["header"]["full_name"] == "Test Candidate"
    [job] = content["experience"]
    assert (job["title"], job["company_name"]) == ("Machine Learning Intern", "Acme Analytics")
    assert (job["start_date"], job["end_date"]) == ("2023-05-01", "2024-05-01")  # unchanged
    assert {b["text"] for b in job["bullets"]} == {
        EVIDENCE["docker"][1],
        EVIDENCE["latency"][1],
    }
    assert [p["title"] for p in content["projects"]] == ["Multi-Agent Research Assistant"]
    assert content["education"][0]["institution"] == "State University"
    assert [c["course_name"] for c in content["coursework"]] == ["Machine Learning"]

    skills = [s["text"] for s in content["skills"]]
    assert set(skills) == {"Python", "Docker"}  # subset of the profile, evidence-backed
    assert any("Terraform" in n for n in resume["notes"])  # listed skill without evidence
    # The cited evidence is returned for the preview, verbatim.
    assert resume["evidence"][ids["docker"]]["content"] == EVIDENCE["docker"][1]

    # Stored separately from the master resume, with an audit trail per claim.
    assert await db.scalar(select(func.count()).select_from(Resume)) == 0
    assert await db.scalar(select(func.count()).select_from(TailoredResume)) == 1
    claims = list(await db.scalars(select(GeneratedClaim)))
    assert len(claims) == len(all_claims(content))
    assert all(c.status == ClaimStatus.VERIFIED for c in claims)
    assert await db.scalar(select(func.count()).select_from(ClaimVerification)) == len(claims)
    links = await db.scalar(select(func.count()).select_from(generated_claim_evidence))
    assert links == sum(len(c["evidence_ids"]) for _, c in all_claims(content))


async def test_empty_profile_produces_no_claims(api: httpx2.AsyncClient) -> None:
    await post(api, PROFILE, {"full_name": "New Candidate"})
    resume = await tailor(api, await build_job(api))
    assert all_claims(resume["content"]) == []
    assert resume["content"]["experience"] == [] and resume["content"]["projects"] == []


async def test_unverified_evidence_is_never_used(api: httpx2.AsyncClient, db: AsyncSession) -> None:
    ids = await build_profile(api)
    evidence = await db.get(CandidateEvidence, ids["docker"])
    assert evidence is not None
    # Extracted from an upload but never confirmed by the candidate.
    unconfirmed = CandidateEvidence(
        candidate_profile_id=evidence.candidate_profile_id,
        source_type=EvidenceSourceType.WORK_EXPERIENCE,
        work_experience_id=evidence.work_experience_id,
        origin=EvidenceOrigin.AI_SUGGESTED,
        content="Led a team of 12 engineers deploying Kubernetes clusters.",
    )
    db.add(unconfirmed)
    await db.flush()
    resume = await tailor(api, await build_job(api))
    assert_grounded(resume, ids)
    assert "12 engineers" not in resume_text(resume)
    assert str(unconfirmed.id) not in resume_text(resume)


# --- A hallucinating model --------------------------------------------------------------


class Hallucinator:
    """Plays a model that embellishes: every kind of invention a resume writer might make,
    alongside one faithful rewording that must survive."""

    name, model = "fake", "fake-model"

    def __init__(self, foreign_evidence: str | None = None) -> None:
        self.foreign = foreign_evidence
        self.calls = 0

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
    ) -> LLMJsonResult:
        self.calls += 1
        found = re.search(r"<candidate_and_job>\n(.*)\n</candidate_and_job>", prompt, re.S)
        assert found is not None
        data = json.loads(found.group(1))
        [job], [project] = data["jobs"], data["projects"]
        ev = {e["text"]: e["evidence_id"] for r in (job, project) for e in r["evidence"]}
        docker, latency = ev[EVIDENCE["docker"][1]], ev[EVIDENCE["latency"][1]]
        rag = ev[EVIDENCE["rag"][1]]
        output = {
            "summary": [
                # Fabricated certification and employer, citing real evidence.
                {"text": "AWS Certified engineer who deployed ML models at Google.",
                 "evidence_ids": [docker]},
                # Cites another candidate's evidence.
                {"text": "Deployed ML models with Docker on AWS.",
                 "evidence_ids": [self.foreign] if self.foreign else []},
            ],
            "skills": ["Python", "Kubernetes", "Terraform", "Docker"],
            "project_ids": ["00000000-0000-0000-0000-000000000000", project["record_id"]],
            "coursework_ids": [],
            "achievement_ids": [],
            "bullets": [
                # Faithful rewording: must be kept.
                {"record_id": job["record_id"], "text": "Deployed ML models on AWS with Docker.",
                 "evidence_ids": [docker]},
                # Inflated metric: rewritten to the evidence.
                {"record_id": job["record_id"],
                 "text": "Reduced model inference latency by 60% using ONNX.",
                 "evidence_ids": [latency]},
                # Exaggerated responsibility and scale.
                {"record_id": project["record_id"],
                 "text": "Spearheaded a RAG pipeline used by millions in Multi-Agent Research "
                         "Assistant.", "evidence_ids": [rag]},
                # Invented technology, citing another record's evidence.
                {"record_id": project["record_id"],
                 "text": "Deployed the assistant on Kubernetes.", "evidence_ids": [docker]},
                # A project the candidate doesn't have.
                {"record_id": "00000000-0000-0000-0000-000000000001",
                 "text": "Built Quantum Trading Platform in 2019.", "evidence_ids": [rag]},
            ],
        }  # fmt: skip
        return LLMJsonResult(output, "fake", "fake-model", 500, 200, 15)


async def test_hallucinated_claims_are_rewritten_or_rejected(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    other = await make_user(db, "someone-else@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Other Candidate"})
        foreign = await post(
            stranger,
            f"{PROFILE}/evidence",
            {"source_type": "profile", "content": "Deployed ML models with Docker on AWS."},
        )

    model = Hallucinator(foreign["id"])
    async with _client(db, user, embedder, model) as api:
        ids = await build_profile(api)
        resume = await tailor(api, await build_job(api))
    assert model.calls == 1
    assert resume["generator"] == "llm:fake-model"
    assert_grounded(resume, ids)
    assert foreign["id"] not in resume_text(resume)

    content = resume["content"]
    # The fabricated sentence is gone. The true one cited another candidate's evidence: that
    # citation is never used, but the engine found the candidate's own evidence for it, and
    # says so explicitly.
    [sentence] = content["summary"]
    assert sentence["text"] == "Deployed ML models with Docker on AWS."
    assert sentence["evidence_ids"] == [ids["docker"]]
    [checked] = [c for c in resume["report"]["claims"] if c["section"] == "summary"]
    assert checked["verification_status"] == "supported"
    assert any("now cites your evidence" in n for n in resume["notes"])
    assert [s["text"] for s in content["skills"]] == ["Python", "Docker"]
    [job] = content["experience"]
    assert [b["text"] for b in job["bullets"]] == [
        "Deployed ML models on AWS with Docker.",  # faithful rewording kept
        EVIDENCE["latency"][1],  # inflated metric rewritten to the evidence
    ]
    [project] = content["projects"]
    assert [b["text"] for b in project["bullets"]] == [EVIDENCE["rag"][1]]

    # Every rejection and rewrite is reported with a reason.
    audit = {a["original_text"]: a for a in resume["verification"]["audit"]}
    expected = {
        "AWS Certified engineer who deployed ML models at Google.": ("rejected", "Certified"),
        "Kubernetes": ("rejected", "skill list"),
        "Terraform": ("rejected", "doesn't mention Terraform"),
        "Reduced model inference latency by 60% using ONNX.": ("rewritten", "says 35%, not 60%"),
        "Spearheaded a RAG pipeline used by millions in Multi-Agent Research Assistant.": (
            "rewritten",
            "millions",
        ),
        "Deployed the assistant on Kubernetes.": ("rejected", "Kubernetes"),
        "Built Quantum Trading Platform in 2019.": ("rejected", "record"),
        "00000000-0000-0000-0000-000000000000": ("rejected", "projects"),
    }
    for original, (outcome, reason) in expected.items():
        assert original in audit, f"not audited: {original}"
        assert audit[original]["outcome"] == outcome, original
        assert reason.lower() in audit[original]["reason"].lower(), audit[original]
    rewritten = audit["Reduced model inference latency by 60% using ONNX."]
    assert rewritten["final_text"] == EVIDENCE["latency"][1]
    assert rewritten["verdict"] == "contradicted"  # the evidence states a different number
    summary = resume["verification"]
    assert summary["rewritten"] == 2 and summary["rejected"] == len(expected) - 2

    # The originals are kept for audit as removed claims, never linked to evidence.
    unsupported = list(
        await db.scalars(select(GeneratedClaim).where(GeneratedClaim.status == "removed"))
    )
    assert {c.claim_text for c in unsupported} == set(expected)
    linked: set[uuid.UUID] = set(
        await db.scalars(select(generated_claim_evidence.c.generated_claim_id))
    )
    assert not linked & {c.id for c in unsupported}

    log = await db.scalar(select(AIExecutionLog))
    assert log is not None and log.status == AIExecutionStatus.SUCCESS
    assert log.operation == "resume_generation" and log.input_tokens == 500
    stored = await db.get(TailoredResume, resume["id"])
    assert stored is not None and stored.ai_execution_log_id == log.id


async def test_model_failure_falls_back_to_rules(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    class Broken:
        name, model = "fake", "fake-model"

        async def complete_json(self, **_: Any) -> LLMJsonResult:
            raise LLMError("the model is unavailable")

    async with _client(db, user, embedder, Broken()) as api:
        ids = await build_profile(api)
        resume = await tailor(api, await build_job(api))
    assert resume["generator"] == "rules"
    assert_grounded(resume, ids)
    assert any("AI tailoring failed" in n for n in resume["notes"])
    log = await db.scalar(select(AIExecutionLog))
    assert log is not None and log.status == AIExecutionStatus.ERROR


async def test_rules_setting_never_calls_the_model(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    model = Hallucinator()
    async with _client(db, user, embedder, model, resume_generator="rules") as api:
        await build_profile(api)
        resume = await tailor(api, await build_job(api))
    assert model.calls == 0 and resume["generator"] == "rules"


# --- Versions, editing, downloads -------------------------------------------------------


async def test_regenerating_replaces_the_draft_with_a_new_version(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    first = await tailor(api, job_id)
    second = await tailor(api, job_id)
    assert second["version"] == 2
    assert (await api.get(f"/api/v1/tailored-resumes/{first['id']}")).status_code == 404
    latest = await api.get(f"{JOBS}/{job_id}/tailored-resumes/latest")
    assert latest.json()["id"] == second["id"]
    assert await db.scalar(select(func.count()).select_from(TailoredResume)) == 1


async def test_supported_edits_are_saved(api: httpx2.AsyncClient) -> None:
    ids = await build_profile(api)
    resume = await tailor(api, await build_job(api))
    content = copy.deepcopy(resume["content"])
    job = content["experience"][0]
    job["bullets"] = [
        {"text": "Cut model inference latency by 35% with ONNX.", "evidence_ids": [ids["latency"]]},
        {"text": EVIDENCE["docker"][1], "evidence_ids": [ids["docker"]]},
    ]
    content["skills"] = content["skills"][:1]

    response = await api.put(f"/api/v1/tailored-resumes/{resume['id']}", json={"content": content})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert_grounded(saved, ids)
    assert saved["content"]["experience"][0]["bullets"][0]["text"] == (
        "Cut model inference latency by 35% with ONNX."
    )
    assert len(saved["content"]["skills"]) == 1
    assert saved["status"] == "verified"
    assert saved["report"]["trigger"] == "edit" and saved["report"]["outcome"] == "approved"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda c: c["experience"][0].update(title="Head of Machine Learning"),
         "title is Machine Learning Intern, not Head of Machine Learning"),
        (lambda c: c["experience"][0].update(start_date="2019-01-01"),
         "start date is 2023-05-01, not 2019-01-01"),
        (lambda c: c["experience"][0].update(company_name="Google"),
         "employer is Acme Analytics, not Google"),
        (lambda c: c["header"].update(full_name="Someone Else"),
         "name is Test Candidate, not Someone Else"),
        (lambda c: c["education"][0].update(degree="M.S."), "degree is B.Tech, not M.S."),
    ],
)  # fmt: skip
async def test_edited_record_facts_are_contradicted_not_silently_corrected(
    api: httpx2.AsyncClient, change: Any, reason: str
) -> None:
    await build_profile(api)
    resume = await tailor(api, await build_job(api))
    content = copy.deepcopy(resume["content"])
    change(content)
    response = await api.put(f"/api/v1/tailored-resumes/{resume['id']}", json={"content": content})
    assert response.status_code == 422, response.text
    [error] = response.json()["detail"]
    assert error["msg"].startswith("Contradicted"), error
    assert reason in error["msg"], error


@pytest.mark.parametrize(
    ("bullet", "field"),
    [
        ({"text": "Cut model inference latency by 80% with ONNX.", "key": "latency"}, "80"),
        (
            {"text": "Led the ML platform team, deploying models with Docker.", "key": "docker"},
            "led",
        ),
        ({"text": "Deployed ML models with Docker and Kubernetes.", "key": "docker"}, "Kubernetes"),
        ({"text": "Built data pipelines in Python and SQL.", "key": "python"}, "not to this item"),
        ({"text": "Won the company hackathon.", "key": None}, "Unsupported"),
        (
            {"text": "Cut model inference latency by 35% with ONNX in 2021.", "key": "latency"},
            "dated 2023\u20132024; the claim mentions 2021",
        ),
        (
            {
                "text": "Deployed ML models with Docker as Senior Engineer at Acme Analytics.",
                "key": "docker",
            },
            "recorded as Machine Learning Intern",
        ),
    ],
)
async def test_unsupported_edits_are_rejected_with_reasons(
    api: httpx2.AsyncClient, bullet: dict[str, Any], field: str
) -> None:
    ids = await build_profile(api)
    resume = await tailor(api, await build_job(api))
    content = copy.deepcopy(resume["content"])
    job = content["experience"][0]
    cited = [ids[bullet["key"]]] if bullet["key"] else []
    job["bullets"].append({"text": bullet["text"], "evidence_ids": cited})

    response = await api.put(f"/api/v1/tailored-resumes/{resume['id']}", json={"content": content})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any(field.lower() in d["msg"].lower() for d in detail), detail
    after = await api.get(f"/api/v1/tailored-resumes/{resume['id']}")
    assert after.json()["content"] == resume["content"]  # nothing saved


async def test_edits_cannot_add_records_the_candidate_does_not_have(
    api: httpx2.AsyncClient,
) -> None:
    await build_profile(api)
    resume = await tailor(api, await build_job(api))
    content = resume["content"]
    content["certifications"].append(
        {
            "record_id": "00000000-0000-0000-0000-000000000002",
            "name": "AWS Certified Solutions Architect",
        }
    )
    response = await api.put(f"/api/v1/tailored-resumes/{resume['id']}", json={"content": content})
    assert response.status_code == 422
    assert "isn't in your profile" in response.text


async def test_downloads_contain_exactly_the_preview_text(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    resume = await tailor(api, await build_job(api))
    base = f"/api/v1/tailored-resumes/{resume['id']}/download"

    pdf = await api.get(base, params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert (
        pdf.headers["content-disposition"] == 'attachment; filename="Test-Candidate-resume-v1.pdf"'
    )
    pdf_text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    docx = await api.get(base, params={"format": "docx"})
    assert docx.status_code == 200
    docx_text = "\n".join(p.text for p in Document(io.BytesIO(docx.content)).paragraphs)

    for text in (
        "Test Candidate",
        "Machine Learning Intern, Acme Analytics",
        "May 2023",
        EVIDENCE["docker"][1],
        "Multi-Agent Research Assistant",
    ):
        assert text in docx_text, text
        assert re.sub(r"\s+", " ", text) in re.sub(r"\s+", " ", pdf_text), text
    for invented in HALLUCINATIONS:
        assert invented not in docx_text and invented not in pdf_text
    assert (await api.get(base, params={"format": "txt"})).status_code == 422


# --- Evidence protection and access -----------------------------------------------------


async def test_cited_evidence_cannot_be_deleted_until_the_resume_is(
    api: httpx2.AsyncClient,
) -> None:
    ids = await build_profile(api)
    job_id = await build_job(api)
    resume = await tailor(api, job_id)
    assert (await api.delete(f"{PROFILE}/evidence/{ids['docker']}")).status_code == 409
    assert (await api.delete(f"{JOBS}/{job_id}")).status_code == 409

    assert (await api.delete(f"/api/v1/tailored-resumes/{resume['id']}")).status_code == 204
    assert (await api.delete(f"{PROFILE}/evidence/{ids['docker']}")).status_code == 204
    assert (await api.delete(f"{JOBS}/{job_id}")).status_code == 204


async def test_resumes_are_private(db: AsyncSession, user: User, embedder: SpyEmbedder) -> None:
    async with _client(db, user, embedder) as api:
        await build_profile(api)
        job_id = await build_job(api)
        resume = await tailor(api, job_id)
    other = await make_user(db, "other@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Other"})
        for response in (
            await stranger.get(f"/api/v1/tailored-resumes/{resume['id']}"),
            await stranger.get(f"/api/v1/tailored-resumes/{resume['id']}/download"),
            await stranger.put(
                f"/api/v1/tailored-resumes/{resume['id']}", json={"content": resume["content"]}
            ),
            await stranger.delete(f"/api/v1/tailored-resumes/{resume['id']}"),
            await stranger.post(f"{JOBS}/{job_id}/tailored-resumes"),
        ):
            assert response.status_code == 404


async def test_latest_is_404_before_generation(api: httpx2.AsyncClient) -> None:
    await post(api, PROFILE, {"full_name": "Test Candidate"})
    job_id = await build_job(api)
    assert (await api.get(f"{JOBS}/{job_id}/tailored-resumes/latest")).status_code == 404
