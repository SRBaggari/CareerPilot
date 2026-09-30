"""Hallucination detection: generated resume claims must be backed by the evidence they cite.

Each case is a realistic way an LLM embellishes a resume. All must be caught; faithful
rewording must still pass.
"""

import uuid
from typing import Any, cast

import pytest

from app.documents.models import VerificationVerdict
from app.documents.resume.content import Claim, ExperienceEntry, Header, ResumeContent
from app.documents.resume.generator import Draft
from app.documents.resume.service import verify_draft
from app.documents.resume.verifier import ClaimKind, EvidenceText, verify_claim
from app.documents.resume.workspace import EvidenceItem, Workspace

RAG = EvidenceText(
    "Implemented a RAG pipeline in Python that cut support ticket triage time by 30%.",
    "Multi-Agent Research Assistant",
)
DOCKER = EvidenceText("Deployed ML models with Docker on AWS.", "Machine Learning Intern at Acme")
TEAM = EvidenceText("Worked with a team of four to build a dashboard in React.", "Capstone")


def check(text: str, *evidence: EvidenceText, kind: ClaimKind = ClaimKind.BULLET) -> Any:
    return verify_claim(text, list(evidence), kind)


# --- Faithful claims pass ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        RAG.content,  # verbatim
        "Built a Python RAG pipeline that cut support ticket triage time by 30%.",
        "Cut support ticket triage time by 30% with a RAG pipeline implemented in Python.",
        "Deployed machine learning models with Docker on AWS.",
    ],
)
def test_faithful_rewording_is_supported(text: str) -> None:
    result = check(text, RAG, DOCKER)
    assert result.verdict == VerificationVerdict.SUPPORTED, result.rationale


def test_a_listed_skill_named_in_the_evidence_is_supported() -> None:
    assert check("Docker", DOCKER, kind=ClaimKind.SKILL).supported
    assert check("Python", RAG, kind=ClaimKind.SKILL).supported


# --- Hallucinations are caught ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        # Invented or changed metrics.
        ("Cut support ticket triage time by 60% with a Python RAG pipeline.", "60"),
        ("Implemented a RAG pipeline serving 10,000 users in Python.", "10000"),
        ("Implemented a RAG pipeline in Python over 3 months.", "3"),
        ("Worked with a team of five to build a dashboard in React.", "5"),
        # Changed or invented dates.
        ("Implemented a RAG pipeline in Python in 2021.", "2021"),
        # Vague inflation.
        ("Deployed dozens of ML models with Docker on AWS.", "dozens"),
        ("Deployed ML models used by millions with Docker on AWS.", "millions"),
        # Invented technologies.
        ("Deployed ML models with Docker and Kubernetes on AWS.", "Kubernetes"),
        ("Implemented a RAG pipeline in Python and Rust.", "Rust"),
        # Exaggerated responsibility.
        ("Led a team of four to build a dashboard in React.", "led"),
        ("Architected a RAG pipeline in Python for support ticket triage.", "architected"),
        ("Managed deployment of ML models with Docker on AWS.", "managed"),
        ("Mentored interns while deploying ML models with Docker on AWS.", "mentored"),
        # Invented employers, products, or names.
        ("Deployed ML models with Docker on AWS for Google.", "Google"),
        # Unrelated content dressed in the evidence's wording.
        ("Designed a fraud detection system for payment transactions.", "fraud"),
    ],
)
def test_hallucinated_claims_are_rejected(text: str, reason: str) -> None:
    result = check(text, RAG, DOCKER, TEAM)
    assert not result.supported, f"{text!r} should be rejected"
    assert reason.lower() in result.rationale.lower(), result.rationale


def test_claims_without_evidence_are_rejected() -> None:
    result = check("Deployed ML models with Docker on AWS.")
    assert result.verdict == VerificationVerdict.UNSUPPORTED
    assert "no evidence" in result.rationale


def test_evidence_must_be_the_cited_evidence_not_any_evidence() -> None:
    # True of the candidate, but the claim cites the wrong statement.
    assert not check("Deployed ML models with Docker on AWS.", RAG).supported


def test_a_skill_the_evidence_does_not_mention_is_rejected() -> None:
    for skill in ("Kubernetes", "Terraform", "Java"):
        result = check(skill, RAG, DOCKER, kind=ClaimKind.SKILL)
        assert not result.supported, skill


def test_a_summary_cannot_claim_seniority_or_years() -> None:
    assert not check(
        "Senior ML engineer with Docker and AWS.", DOCKER, kind=ClaimKind.SUMMARY
    ).supported
    assert not check(
        "ML engineer with 5 years of Docker and AWS deployments.", DOCKER, kind=ClaimKind.SUMMARY
    ).supported


# --- The pipeline step: rewrite or reject -----------------------------------------------


def _workspace(record: uuid.UUID, *items: tuple[uuid.UUID, str, uuid.UUID | None]) -> Workspace:
    ws = Workspace(profile=cast(Any, None), job=cast(Any, None))
    for evidence_id, content, subject in items:
        item = EvidenceItem(evidence_id, content, "Acme", subject, 1.0)
        ws.evidence[evidence_id] = item
        if subject is not None:
            ws.by_subject.setdefault(subject, []).append(item)
    return ws


def test_unsupported_bullets_are_rewritten_to_evidence_and_the_rest_rejected() -> None:
    job, other = uuid.uuid4(), uuid.uuid4()
    docker, rag, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ws = _workspace(
        job, (docker, DOCKER.content, job), (rag, RAG.content, other), (stranger, "x", None)
    )
    content = ResumeContent(
        header=Header(full_name="Test Candidate"),
        summary=[
            Claim(text="Deployed ML models with Docker on AWS.", evidence_ids=[docker]),
            Claim(text="Senior engineer who led ML at Google.", evidence_ids=[docker]),
        ],
        skills=[
            Claim(text="Docker", evidence_ids=[docker]),
            Claim(text="Kubernetes", evidence_ids=[docker]),
        ],
        experience=[
            ExperienceEntry(
                record_id=job,
                title="Machine Learning Intern",
                company_name="Acme",
                bullets=[
                    # Supported rewording: kept.
                    Claim(
                        text="Deployed ML models on AWS with Docker.",
                        evidence_ids=[docker],
                    ),
                    # Invented metric: rewritten to the cited evidence.
                    Claim(text="Deployed 50 ML models with Docker on AWS.", evidence_ids=[docker]),
                    # Cites another record's evidence: not allowed, nothing left -> rejected.
                    Claim(text=RAG.content, evidence_ids=[rag]),
                    # Cites nothing: rejected.
                    Claim(text="Reduced cloud costs by 40%.", evidence_ids=[]),
                ],
            )
        ],
    )
    final, outcomes = verify_draft(Draft(content), ws)

    assert [c.text for c in final.summary] == ["Deployed ML models with Docker on AWS."]
    assert [c.text for c in final.skills] == ["Docker"]
    bullets = [c.text for c in final.experience[0].bullets]
    assert bullets == ["Deployed ML models on AWS with Docker.", DOCKER.content]
    for bullet in final.experience[0].bullets:
        assert bullet.evidence_ids == [docker]

    by_text = {o.original: o for o in outcomes}
    assert by_text["Deployed 50 ML models with Docker on AWS."].final is not None  # rewritten
    for rejected in (
        "Senior engineer who led ML at Google.",
        "Kubernetes",
        RAG.content,
        "Reduced cloud costs by 40%.",
    ):
        assert by_text[rejected].final is None, rejected
    # Nothing in the final resume is unsupported.
    for section, _, claim in final.claims():
        assert claim.evidence_ids, section
