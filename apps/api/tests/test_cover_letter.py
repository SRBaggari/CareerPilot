"""Cover letter generation and hallucination detection, without a database: which
sentences count as factual, generic self-praise, first-person framing, and the generators."""

import uuid
from datetime import date
from typing import Any

import pytest

from app.documents.cover_letter.generator import (
    RuleLetterGenerator,
    _first_person,
    frame,
    map_letter,
)
from app.documents.models import VerificationVerdict
from app.documents.resume.workspace import EvidenceItem, Workspace
from app.jobs.models import Job
from app.profiles.models import CandidateProfile, Project, WorkExperience
from app.verification.compare import ClaimKind, EvidenceText, compare, is_non_factual
from app.verification.engine import assess
from app.verification.knowledge import CandidateKnowledge, Evidence
from app.verification.types import ClaimInput, ClaimType

JOB = {"Senior Machine Learning Engineer", "Northwind Robotics"}

# --- What counts as a factual claim ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Dear Northwind Robotics Hiring Team,",
        "Dear Hiring Manager,",
        "I am writing to apply for the Senior Machine Learning Engineer position at "
        "Northwind Robotics.",
        "I would welcome the opportunity to discuss the role with you.",
        "I look forward to hearing from you.",
        "Thank you for your time and consideration.",
        "Sincerely,",
        "Kind regards,",
        "I am excited about the Senior Machine Learning Engineer role at Northwind Robotics.",
    ],
)
def test_greetings_intent_and_courtesy_are_not_factual_claims(text: str) -> None:
    assert is_non_factual(text, JOB)


@pytest.mark.parametrize(
    "text",
    [
        # Generic qualities presented as fact.
        "I am a passionate, detail-oriented team player.",
        "I would bring strong communication skills to the team.",
        "I am excited to bring my proven track record to Northwind Robotics.",
        # Statements about the candidate's past or abilities.
        "I would bring my experience with Docker to the role.",
        "I am excited to apply my 3 years of Python experience.",
        "I am writing to apply, having led a team of five engineers.",
        "I would like to mention that I have built RAG pipelines.",
        # Greetings and sign-offs that smuggle in claims.
        "Dear Northwind Robotics, from an award-winning engineer,",
        "Dear Northwind Robotics Hiring Team, from a senior engineer,",
        "Sincerely, your future lead engineer",
        # Qualifiers, scale and role words on intent sentences.
        "I am excited to bring production-grade systems to Northwind Robotics.",
        "I would like to lead the team at Northwind Robotics.",
        # Names other than the job and company.
        "I look forward to contributing to Google.",
        "Dear Google Hiring Team,",
        # Technologies are claims about skills.
        "I am eager to use Kubernetes at Northwind Robotics.",
        # Fabrications that open like courtesy sentences (production-readiness review).
        "I'd bring hands-on experience building recommendation systems for hospitals.",
        "I am excited that I interned at a fintech startup last summer.",
        "I would add that my team won the regional robotics championship.",
        "Thank you for considering a candidate who has shipped apps to the app store.",
        "I hope my two years at the lab show my commitment.",
        "I look forward to applying what I learned during my internship.",
        # Not an intent/courtesy sentence at all.
        "Machine learning is my calling.",
        "Deployed ML models with Docker on AWS.",
    ],
)
def test_sentences_that_claim_something_need_evidence(text: str) -> None:
    assert not is_non_factual(text, JOB)


@pytest.mark.parametrize(
    ("text", "trait"),
    [
        ("I am a passionate engineer who deployed ML models with Docker on AWS.", "passionate"),
        ("A hard-working engineer who deployed ML models with Docker on AWS.", "hard-working"),
        ("Deployed ML models with Docker on AWS as a proven team player.", "proven"),
        ("I have strong communication skills and deployed ML models with Docker.", "strong"),
        ("I am a detail-oriented, self-motivated quick learner.", "detail-oriented"),
        ("Brings extensive experience deploying ML models with Docker on AWS.", "extensive"),
    ],
)
def test_generic_self_praise_is_unsupported_unless_the_evidence_says_it(
    text: str, trait: str
) -> None:
    evidence = [EvidenceText("Deployed ML models with Docker on AWS.", "ML Intern at Acme")]
    result = compare(text, evidence, ClaimKind.STATEMENT)
    assert result.verdict == VerificationVerdict.UNSUPPORTED and result.veto
    assert trait in result.reason


def test_a_quality_the_evidence_states_is_fine() -> None:
    evidence = [EvidenceText("Received the Excellent Mentor award from the ML department.")]
    result = compare("Received the Excellent Mentor award.", evidence, ClaimKind.STATEMENT)
    assert result.verdict == VerificationVerdict.SUPPORTED, result.reason


# --- Letters in the engine ---------------------------------------------------------------

DOCKER, OTHER = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def kb() -> CandidateKnowledge:
    profile = CandidateProfile(id=uuid.uuid4(), full_name="Test Candidate")
    job = WorkExperience(
        id=uuid.uuid4(), title="Machine Learning Intern", company_name="Acme Analytics",
        start_date=date(2023, 5, 1), end_date=date(2024, 5, 1), is_current=False,
    )  # fmt: skip
    profile.work_experiences = [job]
    profile.projects, profile.educations, profile.certifications = [], [], []
    profile.achievements, profile.coursework = [], []
    knowledge = CandidateKnowledge(profile=profile, today=date(2026, 9, 30))
    knowledge.verified = {
        DOCKER: Evidence(DOCKER, "Deployed ML models with Docker on AWS.",
                         "Machine Learning Intern at Acme Analytics", job.id),
    }  # fmt: skip
    return knowledge


def letter(text: str, *cited: uuid.UUID) -> ClaimInput:
    return ClaimInput(text, ClaimType.LETTER, list(cited), "paragraphs:1", 0,
                      facts={"allowed_names": sorted(JOB)})  # fmt: skip


def test_intent_sentences_are_approved_without_evidence(kb: CandidateKnowledge) -> None:
    result = assess(letter("I would welcome the opportunity to discuss the role with you."), kb)
    assert result.result.verification_status == VerificationVerdict.SUPPORTED
    assert result.result.evidence_ids == [] and "Not a factual claim" in result.result.reason
    assert result.offered == {}  # never sent to the LLM reviewer


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("As a Machine Learning Intern at Acme Analytics, I deployed ML models with Docker on "
         "AWS.", VerificationVerdict.SUPPORTED),
        ("At Acme Analytics, I deployed 40 ML models with Docker on AWS.",
         VerificationVerdict.UNSUPPORTED),
        ("I won the Acme Innovation Award for deploying ML models.",
         VerificationVerdict.UNSUPPORTED),
        ("I am a passionate team player who deployed ML models with Docker on AWS.",
         VerificationVerdict.UNSUPPORTED),
        ("As a Senior Engineer at Acme Analytics, I deployed ML models with Docker on AWS.",
         VerificationVerdict.CONTRADICTED),
        ("I bring 6 years of experience deploying ML models with Docker.",
         VerificationVerdict.CONTRADICTED),
    ],
)  # fmt: skip
def test_factual_letter_sentences_are_verified(
    kb: CandidateKnowledge, text: str, status: VerificationVerdict
) -> None:
    result = assess(letter(text, DOCKER), kb).result
    assert result.verification_status == status, result.reason


# --- Generators --------------------------------------------------------------------------


def test_first_person_framing_keeps_the_evidence_words() -> None:
    assert _first_person("Deployed ML models with Docker on AWS.") == (
        "I deployed ML models with Docker on AWS"
    )
    assert _first_person("Built a React Native app.") == "I built a React Native app"
    assert _first_person("B.Tech in Computer Science.") is None  # not an action
    assert _first_person("RAG pipeline for search.") is None


def _workspace() -> Workspace:
    profile = CandidateProfile(id=uuid.uuid4(), full_name="Test Candidate",
                               contact_email="t@example.test")  # fmt: skip
    work = WorkExperience(id=uuid.uuid4(), title="Machine Learning Intern",
                          company_name="Acme Analytics")  # fmt: skip
    project = Project(id=uuid.uuid4(), title="Multi-Agent Research Assistant")
    profile.work_experiences, profile.projects, profile.skills = [work], [project], []
    job = Job(title="ML Engineer", company_name="Northwind")
    job.requirements = []
    ws = Workspace(profile=profile, job=job)
    for evidence_id, content, subject, relevance in (
        (DOCKER, "Deployed ML models with Docker on AWS.", work.id, 0.9),
        (OTHER, "Implemented RAG pipeline in Multi-Agent Research Assistant.", project.id, 0.8),
    ):
        item = EvidenceItem(evidence_id, content, "context", subject, relevance)
        ws.evidence[evidence_id] = item
        ws.by_subject.setdefault(subject, []).append(item)
    return ws


def test_the_rule_letter_only_states_facts_from_evidence() -> None:
    ws = _workspace()
    draft = RuleLetterGenerator().generate(ws, None)
    content = draft.content
    assert content.greeting == "Dear Northwind Hiring Team,"
    assert content.paragraphs[0].text == (
        "I am writing to apply for the ML Engineer position at Northwind."
    )
    factual = [s for _, _, s in content.sentences() if s.evidence_ids]
    assert [s.text for s in factual] == [
        "As a Machine Learning Intern at Acme Analytics, I deployed ML models with Docker on AWS.",
        "In my Multi-Agent Research Assistant project, I implemented RAG pipeline in "
        "Multi-Agent Research Assistant.",
    ]
    assert frame(ws.evidence[DOCKER], ws) == factual[0].text
    assert content.word_count() < 150
    assert content.signature.full_name == "Test Candidate"


def test_the_llm_letter_keeps_only_the_candidates_evidence_ids() -> None:
    ws = _workspace()
    data: dict[str, Any] = {
        "greeting": "Dear Northwind Hiring Team,",
        "paragraphs": [
            {"sentences": [
                {"text": "I deployed ML models with Docker on AWS.",
                 "evidence_ids": [str(DOCKER), str(uuid.uuid4()), "not-an-id"]},
                {"text": "   ", "evidence_ids": []},
            ]},
            {"sentences": []},
        ] + [{"sentences": [{"text": f"Sentence {n}.", "evidence_ids": []}]} for n in range(9)],
        "closing": "",
    }  # fmt: skip
    content = map_letter(data, ws).content
    assert content.paragraphs[0].sentences[0].evidence_ids == [DOCKER]
    assert len(content.paragraphs[0].sentences) == 1  # blank sentence dropped
    assert len(content.paragraphs) <= 5  # capped, empty paragraph dropped
    assert content.closing == "Sincerely,"
    assert content.job_title == "ML Engineer" and content.company_name == "Northwind"
