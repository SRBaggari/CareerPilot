"""The claim verification engine end to end: the standalone check endpoint, the LLM
reviewer, stored reports, and re-verification of a resume after the profile changes."""

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIExecutionLog, AIExecutionStatus
from app.ai.provider import LLMError, LLMJsonResult
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.matching import get_match_llm
from app.api.routes.tailored_resumes import get_tailor_llm
from app.api.routes.verification import get_verification_llm
from app.core.config import Settings
from app.documents.models import ClaimVerification, GeneratedClaim
from app.users.models import User
from app.verification.models import VerificationReport

from .conftest import client_for, make_user
from .test_evidence_search import SpyEmbedder
from .test_matching_api import PROFILE, build_job, post
from .test_tailored_resume_api import EVIDENCE, build_profile, tailor

pytestmark = pytest.mark.anyio
CHECK = "/api/v1/verification/check"


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


def _client(
    db: AsyncSession, user: User, embedder: SpyEmbedder, reviewer: Any = None, **settings: Any
) -> httpx2.AsyncClient:
    config = Settings(_env_file=None, app_env="test", **settings)
    overrides = {
        get_embedder: lambda: embedder,
        get_tailor_llm: lambda: None,
        get_match_llm: lambda: None,
        get_verification_llm: lambda: reviewer,
    }
    return client_for(db, user, settings=config, overrides=overrides)


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def check(api: httpx2.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await api.post(CHECK, json=body)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def by_text(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["claim_text"]: c for c in report["claims"]}


# --- The standalone engine --------------------------------------------------------------


async def test_the_specification_examples(api: httpx2.AsyncClient) -> None:
    ids = await build_profile(api)
    report = await check(
        api,
        {
            "claims": [
                {"text": "Built a RAG-based research assistant.", "evidence_ids": [ids["rag"]]},
                {
                    "text": "Built a production RAG platform serving 10,000 users.",
                    "evidence_ids": [ids["rag"]],
                },
            ]
        },
    )
    results = by_text(report)
    supported = results["Built a RAG-based research assistant."]
    assert supported["verification_status"] == "supported"
    assert supported["evidence_ids"] == [ids["rag"]]
    unsupported = results["Built a production RAG platform serving 10,000 users."]
    assert unsupported["verification_status"] == "unsupported"
    assert "10000" in unsupported["reason"]
    assert report["outcome"] == "rejected"
    assert report["counts"] == {
        "supported": 1,
        "partially_supported": 0,
        "unsupported": 1,
        "contradicted": 0,
    }
    for result in report["claims"]:
        for field in (
            "claim_text",
            "claim_type",
            "evidence_ids",
            "verification_status",
            "confidence",
            "reason",
        ):
            assert field in result


async def test_all_four_statuses_from_free_text(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    text = (
        "Deployed ML models with Docker on AWS. "
        "Deployed ML models with Docker and Kubernetes on AWS. "
        "Won first place at a national hackathon. "
        "Reduced model inference latency by 60% using ONNX.\n"
        "- Holds a Master's degree in Computer Science."
    )
    results = by_text(await check(api, {"text": text}))
    assert {t: r["verification_status"] for t, r in results.items()} == {
        "Deployed ML models with Docker on AWS.": "supported",
        "Deployed ML models with Docker and Kubernetes on AWS.": "partially_supported",
        "Won first place at a national hackathon.": "unsupported",
        "Reduced model inference latency by 60% using ONNX.": "contradicted",
        "Holds a Master's degree in Computer Science.": "contradicted",
    }
    # Uncited claims are checked against retrieved evidence, and say which.
    supported = results["Deployed ML models with Docker on AWS."]
    assert supported["evidence_source"] == "retrieved" and supported["evidence_ids"]


async def test_other_candidates_evidence_never_supports_a_claim(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    other = await make_user(db, "other@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Other"})
        theirs = await post(
            stranger,
            f"{PROFILE}/evidence",
            {"source_type": "profile", "content": "Won first place at a national hackathon."},
        )
    async with _client(db, user, embedder) as api:
        await build_profile(api)
        report = await check(
            api,
            {
                "claims": [
                    {
                        "text": "Won first place at a national hackathon.",
                        "evidence_ids": [theirs["id"]],
                    }
                ]
            },
        )
    [result] = report["claims"]
    assert result["verification_status"] == "unsupported"
    assert theirs["id"] not in result["evidence_ids"]
    assert "isn't one of your evidence items" in result["reason"]


async def test_requests_are_validated(api: httpx2.AsyncClient) -> None:
    assert (await api.post(CHECK, json={})).status_code == 422
    assert (await api.post(CHECK, json={"text": "   "})).status_code == 422
    assert (await api.post(CHECK, json={"text": "Built things."})).status_code == 404  # no profile


# --- The LLM reviewer -------------------------------------------------------------------


class Reviewer:
    """A reviewer that approves everything, citing every evidence ID it is offered."""

    name, model = "fake", "fake-reviewer"

    def __init__(self, status: str = "supported") -> None:
        self.status, self.calls = status, 0

    async def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 16000
    ) -> LLMJsonResult:
        self.calls += 1
        found = re.search(r"<claims>\n(.*)\n</claims>", prompt, re.S)
        assert found is not None
        claims = json.loads(found.group(1))
        results = [
            {
                "claim_id": c["claim_id"],
                "status": self.status,
                "reason": "Looks fine.",
                "evidence_ids": [e["evidence_id"] for e in c["evidence"]],
            }
            for c in claims
        ]
        return LLMJsonResult({"results": results}, "fake", "fake-reviewer", 400, 120, 20)


async def test_a_yes_man_reviewer_cannot_approve_hallucinations(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    reviewer = Reviewer()
    async with _client(db, user, embedder, reviewer) as api:
        ids = await build_profile(api)
        report = await check(
            api,
            {
                "claims": [
                    {
                        "text": "Built a production RAG platform serving 10,000 users.",
                        "evidence_ids": [ids["rag"]],
                    },
                    {
                        "text": "Reduced model inference latency by 60% using ONNX.",
                        "evidence_ids": [ids["latency"]],
                    },
                    {
                        "text": "Led the ML platform team at Google.",
                        "evidence_ids": [ids["docker"]],
                    },
                    {
                        "text": "Deployed ML models with Docker and Kubernetes on AWS.",
                        "evidence_ids": [ids["docker"]],
                    },
                ]
            },
        )
    assert reviewer.calls == 1
    assert report["verifier"] == "rules+llm:fake-reviewer"
    assert all(r["verification_status"] != "supported" for r in report["claims"])
    log = await db.scalar(select(AIExecutionLog))
    assert log is not None and log.operation == "claim_verification"
    assert log.status == AIExecutionStatus.SUCCESS


async def test_the_reviewer_can_reject_what_the_rules_accept(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    async with _client(db, user, embedder, Reviewer("contradicted")) as api:
        ids = await build_profile(api)
        report = await check(
            api, {"claims": [{"text": EVIDENCE["docker"][1], "evidence_ids": [ids["docker"]]}]}
        )
    [result] = report["claims"]
    assert result["verification_status"] == "contradicted" and result["method"] == "llm"


async def test_reviewer_failure_falls_back_to_rules_with_a_warning(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    class Broken:
        name, model = "fake", "fake-reviewer"

        async def complete_json(self, **_: Any) -> LLMJsonResult:
            raise LLMError("the reviewer is unavailable")

    async with _client(db, user, embedder, Broken()) as api:
        ids = await build_profile(api)
        report = await check(
            api, {"claims": [{"text": EVIDENCE["docker"][1], "evidence_ids": [ids["docker"]]}]}
        )
    assert report["verifier"] == "rules"
    assert report["claims"][0]["verification_status"] == "supported"
    assert any("reviewer failed" in w for w in report["warnings"])


async def test_rules_setting_never_calls_the_reviewer(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    reviewer = Reviewer()
    async with _client(db, user, embedder, reviewer, claim_verifier="rules") as api:
        await build_profile(api)
        await check(api, {"text": "Deployed ML models with Docker on AWS."})
    assert reviewer.calls == 0


# --- Resumes: stored reports and re-verification ----------------------------------------


async def test_generation_stores_an_approved_report(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await build_profile(api)
    resume = await tailor(api, await build_job(api))
    report = resume["report"]
    assert report["trigger"] == "generation" and report["outcome"] == "approved"
    assert resume["status"] == "verified"
    types = {c["claim_type"] for c in report["claims"]}
    assert {"contact", "employment", "experience", "project", "skill", "education"} <= types
    assert all(c["verification_status"] == "supported" for c in report["claims"])
    history = await api.get(f"/api/v1/tailored-resumes/{resume['id']}/verification-reports")
    assert [r["id"] for r in history.json()] == [report["id"]]
    assert await db.scalar(select(func.count()).select_from(VerificationReport)) == 1


async def test_reverification_catches_profile_changes_without_changing_the_resume(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    ids = await build_profile(api)
    resume = await tailor(api, await build_job(api))
    job_id = ids["records"]["work_experience"]
    # The candidate corrects their job title after the resume was generated.
    response = await api.put(
        f"{PROFILE}/work-experiences/{job_id}",
        json={
            "title": "Data Science Intern",
            "company_name": "Acme Analytics",
            "start_date": "2023-05-01",
            "end_date": "2024-05-01",
        },
    )
    assert response.status_code == 200, response.text

    response = await api.post(f"/api/v1/tailored-resumes/{resume['id']}/verify")
    assert response.status_code == 200, response.text
    checked = response.json()
    assert checked["status"] == "verification_failed"
    assert checked["content"] == resume["content"]  # reported, never silently fixed
    report = checked["report"]
    assert report["trigger"] == "manual" and report["outcome"] == "rejected"
    [employment] = [c for c in report["claims"] if c["claim_type"] == "employment"]
    assert employment["verification_status"] == "contradicted"
    assert "title is Data Science Intern, not Machine Learning Intern" in employment["reason"]

    history = await api.get(f"/api/v1/tailored-resumes/{resume['id']}/verification-reports")
    assert [r["outcome"] for r in history.json()] == ["rejected", "approved"]  # newest first
    claims = list(await db.scalars(select(GeneratedClaim)))
    assert await db.scalar(select(func.count()).select_from(ClaimVerification)) == 2 * len(claims)


async def test_reverification_of_an_intact_resume_stays_verified(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    resume = await tailor(api, await build_job(api))
    checked = (await api.post(f"/api/v1/tailored-resumes/{resume['id']}/verify")).json()
    assert checked["status"] == "verified" and checked["report"]["outcome"] == "approved"


async def test_generation_uses_the_reviewer_and_records_it(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    reviewer = Reviewer("contradicted")  # rejects everything it is shown
    async with _client(db, user, embedder, reviewer) as api:
        await build_profile(api)
        resume = await tailor(api, await build_job(api))
    assert reviewer.calls >= 1
    # Every generated statement was rejected by the reviewer: summary and skills removed,
    # bullets rewritten to their evidence and then rejected again, so the resume fails.
    audit = resume["verification"]["audit"]
    assert audit and all(a["verdict"] == "contradicted" for a in audit)
    assert resume["status"] == "verification_failed"
    assert resume["report"]["outcome"] == "rejected"
    llm_checks = list(
        await db.scalars(select(ClaimVerification).where(ClaimVerification.method == "llm"))
    )
    assert llm_checks and all(c.ai_execution_log_id is not None for c in llm_checks)


async def test_verification_endpoints_are_private(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    async with _client(db, user, embedder) as api:
        await build_profile(api)
        resume = await tailor(api, await build_job(api))
    other = await make_user(db, "intruder@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Intruder"})
        for response in (
            await stranger.post(f"/api/v1/tailored-resumes/{resume['id']}/verify"),
            await stranger.get(f"/api/v1/tailored-resumes/{resume['id']}/verification-reports"),
        ):
            assert response.status_code == 404
