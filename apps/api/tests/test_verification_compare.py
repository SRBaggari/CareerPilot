"""Hallucination detection, evidence comparison level: does the evidence say what the claim
says? Each case is a realistic way generated text embellishes a resume, with the exact
status the engine must give it. Faithful rewording must still pass.
"""

import pytest

from app.documents.models import VerificationVerdict
from app.verification.compare import ClaimKind, EvidenceText, compare

S = VerificationVerdict.SUPPORTED
P = VerificationVerdict.PARTIALLY_SUPPORTED
U = VerificationVerdict.UNSUPPORTED
C = VerificationVerdict.CONTRADICTED

SPEC = EvidenceText(
    "Implemented RAG pipeline for document retrieval and question answering.",
    "Multi-Agent Research Assistant",
)
RAG = EvidenceText(
    "Implemented a RAG pipeline in Python that cut support ticket triage time by 30%.",
    "Multi-Agent Research Assistant",
)
DOCKER = EvidenceText("Deployed ML models with Docker on AWS.", "Machine Learning Intern at Acme")
TEAM = EvidenceText("Worked with a team of four to build a dashboard in React.", "Capstone")
LATENCY = EvidenceText("Reduced model inference latency by 35% using ONNX.", "Acme")
EVAL = EvidenceText(
    "Evaluated answer faithfulness on 200 questions with an automated grading script.",
    "Multi-Agent Research Assistant",
)
ALL = [SPEC, RAG, DOCKER, TEAM, LATENCY, EVAL]


def check(text: str, *evidence: EvidenceText, kind: ClaimKind = ClaimKind.STATEMENT):  # type: ignore[no-untyped-def]
    return compare(text, list(evidence or ALL), kind)


# --- The examples from the specification ------------------------------------------------


def test_spec_example_supported() -> None:
    result = check("Built a RAG-based research assistant.", SPEC)
    assert result.verdict == S, result.reason


def test_spec_example_unsupported() -> None:
    result = check("Built a production RAG platform serving 10,000 users.", SPEC)
    assert result.verdict == U
    assert "10000" in result.reason
    assert result.veto  # an invented metric can't be overruled by any reviewer


# --- Faithful claims pass ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        RAG.content,  # verbatim
        "Built a Python RAG pipeline that cut support ticket triage time by 30%.",
        "Cut support ticket triage time by 30% with a RAG pipeline implemented in Python.",
        "Deployed machine learning models with Docker on AWS.",
        "Worked with a team of four to build a React dashboard.",
        "Evaluated answer faithfulness on 200 questions.",
        "Reduced model inference latency by 35% with ONNX.",
        "Built a RAG pipeline for document retrieval and question answering.",
    ],
)
def test_faithful_rewording_is_supported(text: str) -> None:
    result = check(text)
    assert result.verdict == S, result.reason
    assert not result.veto


def test_skills_named_in_the_evidence_are_supported() -> None:
    for skill, evidence in (
        ("Docker", DOCKER),
        ("Python", RAG),
        ("React", TEAM),
        ("ONNX", LATENCY),
    ):
        assert check(skill, evidence, kind=ClaimKind.SKILL).verdict == S, skill


# --- Hallucinations, with the exact status each must get --------------------------------

HALLUCINATIONS = [
    # Invented metrics and scale: the claim's substance is unsupported.
    ("Built a production RAG platform serving 10,000 users.", U, "10000"),
    ("Implemented a RAG pipeline serving 10,000 users in Python.", U, "10000"),
    ("Implemented a RAG pipeline in Python over 3 months.", U, "3"),
    ("Worked with a team of five to build a dashboard in React.", U, "5"),
    ("Reduced model inference latency by 35% for 2 million users using ONNX.", U, "2"),
    ("Deployed 50 ML models with Docker on AWS.", U, "50"),
    ("Cut triage time by 30% and costs by 20% with a RAG pipeline in Python.", U, "20"),
    ("Deployed dozens of ML models with Docker on AWS.", U, "dozens"),
    ("Deployed ML models used by millions with Docker on AWS.", U, "millions"),
    ("Deployed several ML models with Docker on AWS.", U, "several"),
    # Invented or changed dates.
    ("Implemented a RAG pipeline in Python in 2021.", U, "2021"),
    # A different number for the same thing: the evidence contradicts it.
    ("Reduced model inference latency by 60% using ONNX.", C, "35%, not 60%"),
    ("Evaluated answer faithfulness on 500 questions.", C, "200 questions, not 500 questions"),
    ("Cut support ticket triage time by 45% with a RAG pipeline in Python.", C, "30%"),
    # A real number from the evidence, attached to something it doesn't measure.
    ("Reduced model inference latency by 30% using ONNX.", C, "35%, not 30%"),
    ("Reduced cloud costs by 30% with a RAG pipeline in Python.", U, "something else"),
    # Exaggerated responsibility.
    ("Led a team of four to build a dashboard in React.", U, "led"),
    ("Architected a RAG pipeline in Python for support ticket triage.", U, "architected"),
    ("Managed deployment of ML models with Docker on AWS.", U, "managed"),
    ("Mentored interns while deploying ML models with Docker on AWS.", U, "mentored"),
    ("Spearheaded the RAG pipeline in Python for support ticket triage.", U, "spearheaded"),
    ("Senior engineer who deployed ML models with Docker on AWS.", U, "senior"),
    ("Owned the ML deployment with Docker on AWS.", U, "owned"),
    # Invented employers, products, certifications.
    ("Deployed ML models with Docker on AWS for Google.", U, "Google"),
    ("Implemented a RAG pipeline in Python at Microsoft.", U, "Microsoft"),
    ("AWS Certified engineer who deployed ML models with Docker.", U, "Certified"),
    # Extra technologies on a supported core: partly supported.
    ("Deployed ML models with Docker and Kubernetes on AWS.", P, "Kubernetes"),
    ("Deployed ML models with Docker on AWS using Terraform.", P, "Terraform"),
    # Qualifiers the evidence doesn't state: partly supported.
    ("Built a scalable RAG pipeline for document retrieval and question answering.", P,
     "scalable"),
    ("Deployed production ML models with Docker on AWS.", P, "production"),
    ("Built a robust, enterprise-grade dashboard in React with a team of four.", P, "robust"),
    # Some of the content backed, some not.
    ("Deployed ML models with Docker on AWS and monitored drift.", P, "drift"),
    # Unrelated content dressed in the evidence's wording.
    ("Designed a fraud detection system for payment transactions.", U, "fraud"),
    ("Won first place at a national hackathon.", U, "hackathon"),
    ("Published research on quantum error correction.", U, "quantum"),
]  # fmt: skip


@pytest.mark.parametrize(("text", "status", "reason"), HALLUCINATIONS)
def test_hallucinations_get_the_right_status(
    text: str, status: VerificationVerdict, reason: str
) -> None:
    result = check(text)
    assert result.verdict == status, f"{text!r}: {result.verdict} ({result.reason})"
    assert reason.lower() in result.reason.lower(), result.reason
    assert not result.supported


def test_claims_without_evidence_are_unsupported() -> None:
    result = compare("Deployed ML models with Docker on AWS.", [], ClaimKind.STATEMENT)
    assert result.verdict == U and result.veto
    assert "No verified evidence" in result.reason


def test_the_evidence_must_say_it_not_merely_exist() -> None:
    # True of the candidate, but compared with the wrong statement.
    assert check("Deployed ML models with Docker on AWS.", RAG).verdict == U


@pytest.mark.parametrize("skill", ["Kubernetes", "Terraform", "Java", "Rust", "Spark"])
def test_skills_the_evidence_does_not_mention_are_unsupported(skill: str) -> None:
    result = check(skill, kind=ClaimKind.SKILL)
    assert result.verdict == U and result.veto


def test_a_number_about_something_else_is_not_a_contradiction() -> None:
    # 30% is in the evidence, but about triage time; nothing states a cost figure.
    result = check("Reduced cloud costs by 30% with a RAG pipeline in Python.", RAG)
    assert result.verdict == U and result.veto
    assert "something else" in result.reason


def test_a_metric_named_after_its_number_is_matched_to_the_same_metric() -> None:
    cnn = EvidenceText(
        "Trained a CNN in PyTorch to classify leaf diseases, reaching 92% validation accuracy.",
        "Crop Disease Classifier",
    )
    same = check("I trained a PyTorch model with 92% validation accuracy.", cnn)
    assert same.verdict == S, same.reason
    borrowed = check("I reduced inference latency by 92%.", cnn)
    assert borrowed.verdict != S, borrowed.reason
