"""Hallucination scenarios, end to end.

The candidate has a RAG project, Python projects and college projects. Nothing may turn
those into professional experience, certifications or production systems:

- "RAG project"     must never become "5 years of professional RAG experience."
- "Python project"  must never become "Python certification."
- "College project" must never become "Production system used by 10,000 users."

Checked through the claim checker (rules alone, and with an AI reviewer that vouches for
the claim), through every generator with a model that writes exactly these claims while
citing the candidate's real evidence, through the candidate's own edits, and in the
default (rule-based) documents for all four jobs.
"""

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import LLMJsonResult
from app.api.routes.application_answers import get_answer_llm
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.cover_letters import get_letter_llm
from app.api.routes.matching import get_match_llm
from app.api.routes.tailored_resumes import get_tailor_llm
from app.api.routes.verification import get_verification_llm
from app.core.config import Settings
from app.users.models import User

from ..resume_files import make_docx
from . import e2e_data as data
from .conftest import client_for
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, post
from .test_resume_api import RESUMES

pytestmark = pytest.mark.anyio
PROFILE = "/api/v1/profile"
CHECK = "/api/v1/verification/check"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CLAIMS = list(data.HALLUCINATIONS.values())
# Fragments that must never appear in a verified or approved document.
FORBIDDEN = [
    re.compile(r"\b\d+\+?\s+years?\b.*\b(experience|professional)", re.I),
    re.compile(r"python certifi", re.I),
    re.compile(r"certified in python", re.I),
    re.compile(r"10,?000", re.I),
    re.compile(r"production system", re.I),
]


@dataclass
class HallucinatingLLM:
    """Writes the hallucinations into every text field, citing real evidence IDs from the
    prompt so the claims look grounded, and vouches "supported" when asked to review."""

    name: str = "hallucinating"
    model: str = "hallucinating-1"
    calls: int = 0
    seen_ids: list[str] = field(default_factory=list)

    def _fill(self, schema: dict[str, Any], key: str = "") -> Any:
        if "enum" in schema:
            values = [v for v in schema["enum"] if v is not None]
            return "supported" if "supported" in values else values[0]
        kind = schema.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "object" in kinds:
            return {k: self._fill(v, k) for k, v in schema.get("properties", {}).items()}
        if "array" in kinds:
            if "evidence" in key or key.endswith("ids"):
                return self.seen_ids[:2]
            return [self._fill(schema.get("items", {}), key) for _ in range(len(CLAIMS))]
        if "string" in kinds:
            if key.endswith("id") and self.seen_ids:
                return self.seen_ids[0]
            self.calls += 1
            return CLAIMS[self.calls % len(CLAIMS)]
        if "integer" in kinds or "number" in kinds:
            return 5
        if "boolean" in kinds:
            return True
        return None

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
    ) -> LLMJsonResult:
        self.seen_ids = list(dict.fromkeys(UUID_RE.findall(prompt)))
        return LLMJsonResult(self._fill(schema), self.name, self.model, 10, 10, 1)


def _client(db: AsyncSession, user: User, llm: Any) -> httpx2.AsyncClient:
    embedder = SpyEmbedder()
    overrides: dict[Any, Any] = {get_embedder: lambda: embedder, get_match_llm: lambda: None} | {
        dep: (lambda: llm)
        for dep in (get_tailor_llm, get_letter_llm, get_answer_llm, get_verification_llm)
    }
    settings = Settings(_env_file=None, app_env="test")
    return client_for(db, user, settings=settings, overrides=overrides)


@pytest.fixture
async def rules(db: AsyncSession, user: User) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, None) as client:
        yield client


@pytest.fixture
async def hallucinating(db: AsyncSession, user: User) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, HallucinatingLLM()) as client:
        yield client


async def build_candidate(api: httpx2.AsyncClient) -> None:
    await post(
        api, PROFILE, {"full_name": data.CANDIDATE_NAME, "contact_email": data.CANDIDATE_EMAIL}
    )
    upload = await api.post(
        RESUMES, files={"file": ("aarav.docx", make_docx(data.RESUME_LINES), "application/docx")}
    )
    resume = upload.json()
    suggestions = (await api.get(f"{PROFILE}/suggestions?resume_id={resume['id']}")).json()
    for s in suggestions:
        await api.post(f"{PROFILE}/suggestions/{s['id']}/accept", json={})
    for statement in data.EXTRA_EVIDENCE:
        await post(api, f"{PROFILE}/evidence", {"content": statement, "source_type": "profile"})


async def analyze(api: httpx2.AsyncClient, key: str) -> str:
    job = await post(api, f"{JOBS}/analyze", data.JOBS[key])
    return str(job["id"])


def forbidden_in(text: str) -> list[str]:
    return [p.pattern for p in FORBIDDEN if p.search(text)]


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_text(v) for k, v in value.items() if not k.endswith(("id", "ids")))
    if isinstance(value, list):
        return " ".join(_text(v) for v in value)
    return value if isinstance(value, str) else ""


# --- The claim checker ----------------------------------------------------------------------


async def test_the_claim_checker_rejects_each_hallucination(rules: httpx2.AsyncClient) -> None:
    await build_candidate(rules)
    claims = [{"text": t} for t in [*data.HALLUCINATIONS.values(), *data.TRUTHS.values()]]
    report = await post(rules, CHECK, {"claims": claims})
    status = {c["claim_text"]: c["verification_status"] for c in report["claims"]}
    for name, claim in data.HALLUCINATIONS.items():
        assert status[claim] != "supported", (name, report["claims"])
    for name, claim in data.TRUTHS.items():
        assert status[claim] == "supported", (name, report["claims"])
    assert report["outcome"] == "rejected"


async def test_an_ai_reviewer_cannot_vouch_for_a_hallucination(
    hallucinating: httpx2.AsyncClient,
) -> None:
    await build_candidate(hallucinating)
    # Even citing the real RAG / Python / college evidence, with the reviewer saying
    # "supported", the claims stay unsupported: rules decide upgrades.
    profile = (await hallucinating.get(PROFILE)).json()
    evidence = {e["content"]: e["id"] for p in profile["projects"] for e in p["evidence"]}
    rag = next(i for c, i in evidence.items() if "RAG" in c)
    python = next(i for c, i in evidence.items() if "preprocessing" in c)
    college = next(i for c, i in evidence.items() if "college project" in c)
    claims = [
        {"text": data.HALLUCINATIONS["rag_experience"], "evidence_ids": [rag]},
        {"text": data.HALLUCINATIONS["python_certification"], "evidence_ids": [python]},
        {"text": data.HALLUCINATIONS["production_users"], "evidence_ids": [college]},
    ]
    report = await post(hallucinating, CHECK, {"claims": claims})
    assert all(c["verification_status"] != "supported" for c in report["claims"]), report
    assert report["outcome"] == "rejected"


# --- Generators driven by a hallucinating model ---------------------------------------------


async def test_generated_documents_never_contain_the_hallucinations(
    hallucinating: httpx2.AsyncClient,
) -> None:
    await build_candidate(hallucinating)
    job_id = await analyze(hallucinating, "ai")

    resume = await post(hallucinating, f"{JOBS}/{job_id}/tailored-resumes")
    letter = await post(hallucinating, f"{JOBS}/{job_id}/cover-letters")
    answers = await post(
        hallucinating,
        f"{JOBS}/{job_id}/application-answers",
        {"questions": ["Describe your experience with RAG.", "Why are you a good fit?"]},
    )
    answers = answers if isinstance(answers, list) else answers["answers"]

    documents = {"resume": resume["content"], "cover letter": letter["content"]} | {
        f"answer {n}": a["sentences"] for n, a in enumerate(answers)
    }
    for name, content in documents.items():
        assert forbidden_in(_text(content)) == [], (name, _text(content))
    # The model's claims were caught by verification, not silently lost.
    audited = str(resume["verification"]["audit"]) + str(letter["changes"]["audit"])
    assert any(claim in audited for claim in CLAIMS)


# --- The candidate's own edits --------------------------------------------------------------


async def test_edits_cannot_add_the_hallucinations(rules: httpx2.AsyncClient) -> None:
    await build_candidate(rules)
    job_id = await analyze(rules, "ai")
    resume = await post(rules, f"{JOBS}/{job_id}/tailored-resumes")
    letter = await post(rules, f"{JOBS}/{job_id}/cover-letters")
    answers = await post(
        rules,
        f"{JOBS}/{job_id}/application-answers",
        {"questions": ["Describe your experience with Python."]},
    )
    answer = (answers if isinstance(answers, list) else answers["answers"])[0]
    rag_ids = (
        resume["content"]["summary"][0]["evidence_ids"] if resume["content"]["summary"] else []
    )

    for claim in CLAIMS:
        content = resume["content"]
        content["summary"] = [{"text": claim, "evidence_ids": rag_ids}]
        edited = await rules.put(
            f"/api/v1/tailored-resumes/{resume['id']}", json={"content": content}
        )
        assert edited.status_code == 422, (claim, edited.text)

        letter_edit = {
            "greeting": letter["content"]["greeting"],
            "paragraphs": [claim],
            "closing": letter["content"]["closing"],
        }
        edited = await rules.put(f"/api/v1/cover-letters/{letter['id']}", json=letter_edit)
        assert edited.status_code == 422, (claim, edited.text)

        edited = await rules.put(
            f"/api/v1/application-answers/{answer['id']}", json={"answer": claim}
        )
        assert edited.status_code == 422, (claim, edited.text)


# --- Default (rule-based) documents for all four jobs ---------------------------------------


async def test_default_documents_for_every_job_stay_within_the_evidence(
    rules: httpx2.AsyncClient,
) -> None:
    await build_candidate(rules)
    for key in data.JOBS:
        job_id = await analyze(rules, key)
        resume = await post(rules, f"{JOBS}/{job_id}/tailored-resumes")
        letter = await post(rules, f"{JOBS}/{job_id}/cover-letters")
        for name, content in (("resume", resume["content"]), ("letter", letter["content"])):
            assert forbidden_in(_text(content)) == [], (key, name, _text(content))
        assert resume["status"] == "verified" and letter["status"] == "verified", key
        # No certification the candidate doesn't hold.
        certs = {c["name"] for c in resume["content"]["certifications"]}
        assert certs <= {
            "Machine Learning Specialization",
            "Generative AI with Large Language Models",
        }, certs


# Paraphrases of the same hallucinations, and true claims worded differently from the
# evidence: rejected and accepted respectively.
VARIANTS = {
    "I have five years of RAG experience.": False,
    "I have 3+ years of professional experience building RAG systems.": False,
    "I am a senior RAG engineer.": False,
    "I am a certified Python developer.": False,
    "I earned a Python certification from Coursera.": False,
    "I hold the PCAP Python certification.": False,
    "My RAG project is used by thousands of users.": False,
    "I deployed DocuMind to production for 10,000 students.": False,
    "My crop disease classifier is used in production by farmers.": False,
    "I led a team of 5 engineers on the DocuMind project.": False,
    "I worked as a Machine Learning Engineer at Google.": False,
    "My RAG pipeline reached 99% accuracy.": False,
    "I built a RAG pipeline in Python.": True,
    "I trained a PyTorch model with 92% validation accuracy.": True,
}


async def test_paraphrased_hallucinations_are_rejected_and_true_claims_kept(
    rules: httpx2.AsyncClient,
) -> None:
    await build_candidate(rules)
    report = await post(rules, CHECK, {"claims": [{"text": t} for t in VARIANTS]})
    for claim in report["claims"]:
        supported = claim["verification_status"] == "supported"
        assert supported == VARIANTS[claim["claim_text"]], claim
