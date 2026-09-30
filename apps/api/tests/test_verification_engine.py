"""Hallucination detection, engine level: citations, retrieval, the stored profile, record
facts, and the LLM reviewer's limits. Uses in-memory candidate knowledge (no database)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.documents.models import VerificationVerdict
from app.documents.resume.content import (
    Claim,
    EducationEntry,
    ExperienceEntry,
    Header,
    ProjectEntry,
    ResumeContent,
)
from app.profiles.models import CandidateProfile, DegreeLevel, Education, Project, WorkExperience
from app.verification.engine import apply_review, assess
from app.verification.extraction import extract_resume_claims, extract_text_claims
from app.verification.knowledge import CandidateKnowledge, Evidence
from app.verification.llm import Review
from app.verification.types import ClaimInput, ClaimType, build_report

S = VerificationVerdict.SUPPORTED
P = VerificationVerdict.PARTIALLY_SUPPORTED
U = VerificationVerdict.UNSUPPORTED
C = VerificationVerdict.CONTRADICTED

JOB, PROJECT, SCHOOL = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
RAG, DOCKER, LATENCY, UNCONFIRMED, BARE = (uuid.uuid4() for _ in range(5))


@pytest.fixture
def kb() -> CandidateKnowledge:
    profile = CandidateProfile(
        id=uuid.uuid4(), full_name="Test Candidate", contact_email="t@example.test"
    )
    profile.work_experiences = [
        WorkExperience(
            id=JOB, title="Machine Learning Intern", company_name="Acme Analytics",
            start_date=date(2023, 5, 1), end_date=date(2024, 5, 1), is_current=False,
        )
    ]  # fmt: skip
    profile.projects = [Project(id=PROJECT, title="Multi-Agent Research Assistant")]
    profile.educations = [
        Education(
            id=SCHOOL, institution="State University", degree="B.Tech",
            degree_level=DegreeLevel.BACHELOR, field_of_study="Computer Science",
            end_date=date(2023, 6, 1), gpa=Decimal("8.70"), gpa_scale=Decimal("10.00"),
        )
    ]  # fmt: skip
    profile.certifications, profile.achievements, profile.coursework = [], [], []
    project_label = "Multi-Agent Research Assistant"
    knowledge = CandidateKnowledge(profile=profile, today=date(2026, 9, 30))
    knowledge.verified = {
        RAG: Evidence(RAG, "Implemented RAG pipeline for document retrieval and question "
                      "answering.", project_label, PROJECT),
        DOCKER: Evidence(DOCKER, "Deployed ML models with Docker on AWS.",
                         "Machine Learning Intern at Acme Analytics", JOB),
        LATENCY: Evidence(LATENCY, "Reduced model inference latency by 35% using ONNX.",
                          "Machine Learning Intern at Acme Analytics", JOB),
        BARE: Evidence(BARE, "Implemented RAG pipeline for document retrieval and question "
                       "answering.", "", None),
    }  # fmt: skip
    knowledge.unverified = {
        UNCONFIRMED: Evidence(UNCONFIRMED, "Led a team of 12 engineers building Kubernetes "
                              "clusters.", "Machine Learning Intern at Acme Analytics", JOB),
    }  # fmt: skip
    return knowledge


def claim(text: str, *cited: uuid.UUID, kind: ClaimType = ClaimType.SUMMARY,
          record: uuid.UUID | None = None) -> ClaimInput:  # fmt: skip
    section = {ClaimType.EXPERIENCE: f"experience:{record}", ClaimType.PROJECT:
               f"projects:{record}"}.get(kind, kind.value)  # fmt: skip
    return ClaimInput(text, kind, list(cited), section, 0, record)


# --- The specification's examples and result shape --------------------------------------


def test_spec_example_supported(kb: CandidateKnowledge) -> None:
    result = assess(claim("Built a RAG-based research assistant.", RAG), kb).result
    assert result.verification_status == S
    assert result.approved and result.evidence_ids == [RAG] and result.evidence_source == "cited"
    assert result.method == "rule_based" and result.confidence > 0.6


def test_spec_example_unsupported(kb: CandidateKnowledge) -> None:
    result = assess(claim("Built a production RAG platform serving 10,000 users.", RAG), kb).result
    assert result.verification_status == U and not result.approved
    assert "10000" in result.reason


def test_every_result_has_the_required_fields(kb: CandidateKnowledge) -> None:
    result = assess(claim("Deployed ML models with Docker on AWS.", DOCKER), kb).result
    data = result.model_dump()
    for name in ("claim_text", "claim_type", "evidence_ids", "verification_status",
                 "confidence", "reason"):  # fmt: skip
        assert data[name] not in (None, ""), name
    assert 0.0 <= result.confidence <= 1.0


# --- Citations: only the candidate's own verified evidence counts -----------------------


def test_unconfirmed_evidence_never_supports_a_claim(kb: CandidateKnowledge) -> None:
    # Word for word what the unconfirmed evidence says, and still not supported.
    text = "Led a team of 12 engineers building Kubernetes clusters."
    result = assess(claim(text, UNCONFIRMED), kb).result
    assert result.verification_status == U
    assert UNCONFIRMED not in result.evidence_ids
    assert "haven't confirmed" in result.reason


def test_foreign_evidence_ids_are_reported_and_ignored(kb: CandidateKnowledge) -> None:
    result = assess(claim("Won the national hackathon.", uuid.uuid4()), kb).result
    assert result.verification_status == U
    assert "isn't one of your evidence items" in result.reason
    assert result.evidence_ids == []


def test_a_bullet_cannot_borrow_another_items_evidence(kb: CandidateKnowledge) -> None:
    text = "Implemented RAG pipeline for document retrieval and question answering."
    result = assess(claim(text, RAG, kind=ClaimType.EXPERIENCE, record=JOB), kb).result
    assert result.verification_status != S
    assert "belongs to Multi-Agent Research Assistant" in result.reason


def test_no_evidence_at_all_is_unsupported(kb: CandidateKnowledge) -> None:
    result = assess(claim("Deployed ML models with Docker on AWS."), kb).result
    assert result.verification_status == U and result.evidence_ids == []
    assert "No verified evidence" in result.reason


def test_uncited_claims_can_be_supported_by_retrieved_evidence_explicitly(
    kb: CandidateKnowledge,
) -> None:
    result = assess(claim("Deployed ML models with Docker on AWS."), kb, [DOCKER]).result
    assert result.verification_status == S
    assert result.evidence_ids == [DOCKER] and result.evidence_source == "retrieved"
    assert "not the evidence it cited" in result.reason
    assert result.confidence < 1.0


def test_retrieval_is_limited_to_the_bullets_own_item(kb: CandidateKnowledge) -> None:
    text = "Implemented RAG pipeline for document retrieval and question answering."
    result = assess(claim(text, kind=ClaimType.EXPERIENCE, record=JOB), kb, [RAG, BARE]).result
    assert result.verification_status != S  # RAG evidence belongs to the project


def test_cited_contradiction_is_reported_even_if_other_evidence_is_weak(
    kb: CandidateKnowledge,
) -> None:
    text = "Reduced model inference latency by 60% using ONNX."
    result = assess(claim(text, LATENCY), kb, [DOCKER]).result
    assert result.verification_status == C
    assert "35%, not 60%" in result.reason and result.evidence_ids == [LATENCY]


# --- Contradictions with the stored profile ---------------------------------------------

CONTRADICTIONS = [
    ("Machine learning engineer with 5+ years of experience deploying models.",
     "adds up to about 1.0 years"),
    ("Brings 3 years of professional experience in ML.", "about 1.0 years"),
    ("Holds a Master's degree in Computer Science.", "B.Tech"),
    ("PhD in machine learning from State University.", "B.Tech"),
    ("Graduated with a GPA of 9.5.", "recorded GPA is 8.7"),
    ("Worked as a Senior Engineer at Acme Analytics.", "recorded as Machine Learning Intern"),
    ("Lead ML engineer at Acme Analytics deploying models.", "not lead"),
]  # fmt: skip


@pytest.mark.parametrize(("text", "reason"), CONTRADICTIONS)
def test_claims_that_conflict_with_the_profile_are_contradicted(
    kb: CandidateKnowledge, text: str, reason: str
) -> None:
    result = assess(claim(text, DOCKER, RAG), kb).result
    assert result.verification_status == C, result.reason
    assert reason in result.reason
    assert result.evidence_source == "profile"


@pytest.mark.parametrize(
    "text",
    [
        "Deployed ML models with Docker on AWS, cutting latency to 200 ms.",  # "ms" isn't a degree
        "Machine learning intern with 1 year of experience.",
        "B.Tech in Computer Science from State University.",
        "Graduated with a GPA of 8.7.",
        "Machine Learning Intern at Acme Analytics.",
    ],
)
def test_consistent_statements_are_not_contradicted(kb: CandidateKnowledge, text: str) -> None:
    result = assess(claim(text, DOCKER), kb).result
    assert result.verification_status != C, result.reason


def test_a_year_outside_the_items_dates_is_contradicted(kb: CandidateKnowledge) -> None:
    text = "Deployed ML models with Docker on AWS in 2021."
    result = assess(claim(text, DOCKER, kind=ClaimType.EXPERIENCE, record=JOB), kb).result
    assert result.verification_status == C
    assert "dated 2023\u20132024; the claim mentions 2021" in result.reason


# --- Record facts -----------------------------------------------------------------------


def _resume(**changes: object) -> ResumeContent:
    job = ExperienceEntry(
        record_id=JOB, title="Machine Learning Intern", company_name="Acme Analytics",
        start_date=date(2023, 5, 1), end_date=date(2024, 5, 1),
        bullets=[Claim(text="Deployed ML models with Docker on AWS.", evidence_ids=[DOCKER])],
    )  # fmt: skip
    for key, value in changes.items():
        setattr(job, key, value)
    return ResumeContent(
        header=Header(full_name="Test Candidate", contact_email="t@example.test"),
        experience=[job],
        projects=[ProjectEntry(record_id=PROJECT, title="Multi-Agent Research Assistant")],
        education=[
            EducationEntry(
                record_id=SCHOOL, institution="State University", degree="B.Tech",
                field_of_study="Computer Science", end_date=date(2023, 6, 1),
                gpa=Decimal("8.70"), gpa_scale=Decimal("10.00"),
            )
        ],
    )  # fmt: skip


def _record_results(kb: CandidateKnowledge, content: ResumeContent) -> dict[str, object]:
    return {
        f"{c.claim_type}": assess(c, kb).result
        for c in extract_resume_claims(content)
        if c.is_record_fact
    }


def test_record_facts_copied_from_the_profile_are_supported(kb: CandidateKnowledge) -> None:
    results = _record_results(kb, _resume())
    assert set(results) == {"contact", "employment", "project_entry", "education"}
    for result in results.values():
        assert result.verification_status == S, result.reason  # type: ignore[attr-defined]
        assert result.evidence_source == "profile"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"title": "Senior ML Engineer"},
            "title is Machine Learning Intern, not Senior ML Engineer",
        ),
        ({"company_name": "Google"}, "employer is Acme Analytics, not Google"),
        ({"start_date": date(2021, 1, 1)}, "start date is 2023-05-01, not 2021-01-01"),
        ({"end_date": None, "is_current": True}, "current role is False, not True"),
    ],
)
def test_changed_record_facts_are_contradicted(
    kb: CandidateKnowledge, change: dict[str, object], reason: str
) -> None:
    result = _record_results(kb, _resume(**change))["employment"]
    assert result.verification_status == C  # type: ignore[attr-defined]
    assert reason in result.reason  # type: ignore[attr-defined]


def test_records_the_candidate_does_not_have_are_unsupported(kb: CandidateKnowledge) -> None:
    content = _resume()
    content.experience[0].record_id = uuid.uuid4()
    result = _record_results(kb, content)["employment"]
    assert result.verification_status == U  # type: ignore[attr-defined]
    assert "isn't in your profile" in result.reason  # type: ignore[attr-defined]


# --- Extraction and the report ----------------------------------------------------------


def test_extraction_covers_every_statement_in_a_resume() -> None:
    content = _resume()
    content.summary = [Claim(text="ML intern.", evidence_ids=[DOCKER])]
    content.skills = [Claim(text="Docker", evidence_ids=[DOCKER])]
    types = [c.claim_type for c in extract_resume_claims(content)]
    assert types == [
        ClaimType.CONTACT, ClaimType.SUMMARY, ClaimType.SKILL, ClaimType.EMPLOYMENT,
        ClaimType.EXPERIENCE, ClaimType.PROJECT_ENTRY, ClaimType.EDUCATION,
    ]  # fmt: skip


def test_free_text_is_split_into_one_claim_per_sentence() -> None:
    text = (
        "Built a RAG-based research assistant. Deployed ML models with Docker on AWS.\n"
        "- Reduced latency by 35% using ONNX.\n\u2022 Won the hackathon!"
    )
    assert [c.text for c in extract_text_claims(text)] == [
        "Built a RAG-based research assistant.",
        "Deployed ML models with Docker on AWS.",
        "Reduced latency by 35% using ONNX.",
        "Won the hackathon!",
    ]


def test_a_report_is_approved_only_if_every_claim_is_supported(kb: CandidateKnowledge) -> None:
    good = assess(claim("Deployed ML models with Docker on AWS.", DOCKER), kb).result
    partial = assess(claim("Deployed ML models with Docker and Kubernetes on AWS.", DOCKER), kb)
    assert build_report([good], verifier="rules", document_type="text").outcome == "approved"
    report = build_report([good, partial.result], verifier="rules", document_type="text")
    assert report.outcome == "rejected"
    assert report.counts[S] == 1 and report.counts[P] == 1


# --- The LLM reviewer can make verdicts stricter, never overrule a fact check -----------


def review(status: VerificationVerdict, *ids: uuid.UUID, reason: str = "Looks right.") -> Review:
    return Review(status, [str(i) for i in ids], reason)


def test_reviewer_can_accept_a_faithful_paraphrase(kb: CandidateKnowledge) -> None:
    assessment = assess(claim("Built a RAG-based research assistant.", BARE), kb)
    assert assessment.result.verification_status == U and not assessment.veto  # wording only
    result = apply_review(assessment, review(S, BARE), kb, "fake-model")
    assert result.verification_status == S and result.method == "llm"
    assert result.evidence_ids == [BARE] and result.confidence == 0.75
    assert "AI reviewer (fake-model)" in result.reason and "Rule check" in result.reason


def test_reviewer_cannot_overrule_a_failed_fact_check(kb: CandidateKnowledge) -> None:
    assessment = assess(claim("Built a production RAG platform serving 10,000 users.", RAG), kb)
    result = apply_review(assessment, review(S, RAG), kb, "fake-model")
    assert result.verification_status == U and result.method == "rule_based"
    assert "not approved" in result.reason


def test_reviewer_must_cite_offered_evidence_to_upgrade(kb: CandidateKnowledge) -> None:
    assessment = assess(claim("Built a RAG-based research assistant.", BARE), kb)
    for ids in ((), (uuid.uuid4(),), (UNCONFIRMED,)):
        result = apply_review(assessment, review(S, *ids), kb, "fake-model")
        assert result.verification_status == U, ids


def test_reviewer_cannot_upgrade_unrelated_content(kb: CandidateKnowledge) -> None:
    assessment = assess(claim("Designed a fraud detection system for payments.", DOCKER), kb)
    result = apply_review(assessment, review(S, DOCKER), kb, "fake-model")
    assert result.verification_status == U


def test_reviewer_can_always_make_a_verdict_stricter(kb: CandidateKnowledge) -> None:
    assessment = assess(claim("Deployed ML models with Docker on AWS.", DOCKER), kb)
    assert assessment.result.verification_status == S
    result = apply_review(assessment, review(C, DOCKER, reason="Wrong cloud."), kb, "fake-model")
    assert result.verification_status == C and result.method == "llm"
    assert result.reason == "AI reviewer (fake-model): Wrong cloud."


def test_reviewer_cannot_approve_record_facts(kb: CandidateKnowledge) -> None:
    [employment] = [
        c for c in extract_resume_claims(_resume(title="CTO")) if c.claim_type == "employment"
    ]
    assessment = assess(employment, kb)
    result = apply_review(assessment, review(S), kb, "fake-model")
    assert result.verification_status == C


ALWAYS_YES_CASES = [
    "Built a production RAG platform serving 10,000 users.",
    "Led a team of 12 engineers building Kubernetes clusters.",
    "Reduced model inference latency by 60% using ONNX.",
    "Deployed ML models with Docker on AWS for Google.",
    "Deployed ML models with Docker and Kubernetes on AWS.",
    "Built a scalable RAG pipeline for document retrieval and question answering.",
    "Designed a fraud detection system for payment transactions.",
    "Holds a Master's degree in Computer Science.",
    "Machine learning engineer with 5+ years of experience deploying models.",
    "Won first place at a national hackathon.",
]


@pytest.mark.parametrize("text", ALWAYS_YES_CASES)
def test_a_reviewer_that_approves_everything_cannot_approve_hallucinations(
    kb: CandidateKnowledge, text: str
) -> None:
    assessment = assess(claim(text, RAG, DOCKER, LATENCY), kb, [BARE])
    everything = [*assessment.offered]
    result = apply_review(assessment, review(S, *everything), kb, "fake-model")
    assert result.verification_status != S, (text, result.reason)
