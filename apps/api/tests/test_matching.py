"""Matching engine: semantic/concept judging, structured checks, grounding, scoring."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.jobs.models import JobRequirement, RequirementImportance, RequirementType
from app.matching.engine.facts import CandidateFacts, EducationFact, _merged_years
from app.matching.engine.judge import Assessment, RuleJudge, required_degree
from app.matching.engine.llm_judge import JUDGE_SCHEMA, build_prompt, ground_assessment
from app.matching.engine.scoring import score
from app.matching.models import MatchStatus
from app.profiles.models import DegreeLevel, EvidenceOrigin, EvidenceSourceType, VerificationStatus
from app.retrieval.schemas import EvidenceSource, RetrievedEvidence

M, P, X, U = MatchStatus.MATCHED, MatchStatus.PARTIAL, MatchStatus.MISSING, MatchStatus.UNKNOWN
REQ, PREF = RequirementImportance.REQUIRED, RequirementImportance.PREFERRED
T = RequirementType
JUDGE = RuleJudge((0.45, 0.2))  # the hashing embedder's thresholds


def requirement(
    kind: RequirementType, text: str, importance: RequirementImportance = REQ, **extra: Any
) -> JobRequirement:
    return JobRequirement(
        id=uuid.uuid4(),
        requirement_type=kind,
        importance=importance,
        description=text,
        sort_order=0,
        **extra,
    )


def evidence(
    content: str,
    similarity: float,
    label: str | None = None,
    kind: EvidenceSourceType = EvidenceSourceType.PROJECT,
    record_id: uuid.UUID | None = None,
) -> RetrievedEvidence:
    now = datetime.now(UTC)
    return RetrievedEvidence(
        evidence_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        evidence_type=kind,
        factual_content=content,
        similarity=similarity,
        confidence="high",
        verification_status=VerificationStatus.VERIFIED,
        source=EvidenceSource(
            record_type=kind,
            record_id=record_id,
            record_label=label,
            origin=EvidenceOrigin.USER_ENTERED,
            resume_id=None,
            resume_file_name=None,
        ),
        created_at=now,
        updated_at=now,
    )


def facts(**overrides: Any) -> CandidateFacts:
    return CandidateFacts(profile_id=uuid.uuid4(), **overrides)


# --- The example from the brief ---------------------------------------------------------


def test_rag_requirement_is_matched_by_rag_pipeline_evidence() -> None:
    rag = evidence("Implemented RAG pipeline in Multi-Agent Research Assistant.", 0.12)
    other = evidence("Mentored 60 students in data structures labs.", 0.05)
    result = JUDGE.assess(
        requirement(T.SKILL, "Experience with Retrieval-Augmented Generation"),
        [rag, other],
        facts(),
    )
    assert result.status == M
    assert result.evidence_ids == [rag.evidence_id]  # only the evidence that shows it
    assert "RAG" in result.explanation and "Implemented RAG pipeline" in result.explanation


# --- Technologies -----------------------------------------------------------------------


def test_technology_named_in_evidence_is_matched() -> None:
    ev = evidence("Built data pipelines in Python and SQL.", 0.4)
    result = JUDGE.assess(requirement(T.TECHNOLOGY, "Python"), [ev], facts())
    assert (result.status, result.evidence_ids) == (M, [ev.evidence_id])


def test_listed_skill_without_evidence_is_only_partial_and_cites_nothing() -> None:
    skill_id = uuid.uuid4()
    result = JUDGE.assess(
        requirement(T.TECHNOLOGY, "Terraform", skill_id=skill_id), [], facts(skill_ids={skill_id})
    )
    assert (result.status, result.evidence_ids, result.basis) == (P, [], "profile")
    assert "skills list" in result.explanation


def test_related_evidence_that_doesnt_name_the_technology_is_partial() -> None:
    ev = evidence("Deployed container workloads to a managed cluster.", 0.3)
    result = JUDGE.assess(requirement(T.TECHNOLOGY, "Kubernetes"), [ev], facts())
    assert result.status == P and "doesn't mention Kubernetes" in result.explanation


def test_absent_technology_is_missing() -> None:
    result = JUDGE.assess(
        requirement(T.TECHNOLOGY, "Kubernetes"),
        [evidence("Taught Python workshops.", 0.05)],
        facts(),
    )
    assert (result.status, result.evidence_ids) == (X, [])


def test_technology_aliases_count_as_the_same_concept() -> None:
    ev = evidence("Ran services on k8s with Helm charts.", 0.1)
    assert JUDGE.assess(requirement(T.TECHNOLOGY, "Kubernetes"), [ev], facts()).status == M


# --- Statements -------------------------------------------------------------------------


def test_statement_with_several_concepts_is_partial_when_only_some_are_covered() -> None:
    ev = evidence("Deployed the model with Docker.", 0.3)
    result = JUDGE.assess(
        requirement(T.SKILL, "Experience with Docker and Kubernetes"), [ev], facts()
    )
    assert result.status == P
    assert "Docker" in result.explanation and "not Kubernetes" in result.explanation


def test_semantically_close_evidence_matches_a_statement_without_named_technologies() -> None:
    ev = evidence("Presented research findings to 200 conference attendees.", 0.6)
    result = JUDGE.assess(
        requirement(T.SKILL, "Present findings to large audiences"), [ev], facts()
    )
    assert result.status == M and result.evidence_ids == [ev.evidence_id]


def test_unevidenced_soft_skill_is_unknown_not_missing() -> None:
    result = JUDGE.assess(
        requirement(T.SKILL, "Excellent written communication"),
        [evidence("Built a crawler.", 0.02)],
        facts(),
    )
    assert (result.status, result.evidence_ids) == (U, [])


def test_certification_listed_in_profile_without_evidence_is_partial() -> None:
    result = JUDGE.assess(
        requirement(T.CERTIFICATION, "AWS Certified Cloud Practitioner"),
        [],
        facts(certifications={uuid.uuid4(): "AWS Certified Cloud Practitioner"}),
    )
    assert (result.status, result.basis) == (P, "profile")


# --- Experience years -------------------------------------------------------------------


def test_too_few_years_caps_a_relevant_match_at_partial() -> None:
    ev = evidence("Built machine learning systems for demand forecasting.", 0.5)
    req = requirement(
        T.EXPERIENCE, "3+ years building machine learning systems", min_years=Decimal(3)
    )
    result = JUDGE.assess(req, [ev], facts(experience_years=1.2))
    assert result.status == P and result.cap == P
    assert "1.2 years" in result.explanation and result.details["required_years"] == 3.0
    assert JUDGE.assess(req, [ev], facts(experience_years=4.0)).status == M
    assert "no dates" in JUDGE.assess(req, [ev], facts(experience_years=None)).explanation


def test_overlapping_jobs_are_not_double_counted() -> None:
    ranges = [
        (date(2020, 1, 1), date(2021, 1, 1)),
        (date(2020, 7, 1), date(2021, 7, 1)),
        (date(2023, 1, 1), date(2024, 1, 1)),
    ]
    assert _merged_years(ranges) == 2.5


# --- Education and eligibility (structured checks) --------------------------------------


def edu(level: DegreeLevel | None, **extra: Any) -> EducationFact:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "institution": "State University",
        "degree": "B.Tech",
        "degree_level": level,
        "end_date": None,
        "gpa": None,
        "gpa_scale": None,
    }
    return EducationFact(**{**base, **extra})


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("Bachelor's degree in Computer Science", DegreeLevel.BACHELOR),
        ("Bachelor's or Master's degree", DegreeLevel.BACHELOR),
        ("MS or PhD in ML", DegreeLevel.MASTER),
        ("B.Tech / M.Tech", DegreeLevel.BACHELOR),
        ("A degree in a quantitative field", None),
        ("Must keep p99 latency under 10 ms", None),
    ],
)
def test_required_degree(text: str, level: DegreeLevel | None) -> None:
    assert required_degree(text) == level


def test_education_level_check() -> None:
    req = requirement(T.EDUCATION, "Bachelor's degree in Computer Science")
    master = edu(DegreeLevel.MASTER)
    cited = uuid.uuid4()
    with_evidence = JUDGE.assess(
        req, [], facts(educations=[master], evidence_by_subject={master.id: [cited]})
    )
    assert (with_evidence.status, with_evidence.evidence_ids) == (M, [cited])
    assert JUDGE.assess(req, [], facts(educations=[master])).status == P  # nothing to cite
    assert JUDGE.assess(req, [], facts(educations=[edu(DegreeLevel.DIPLOMA)])).status == X
    assert JUDGE.assess(req, [], facts()).status == X
    assert JUDGE.assess(req, [], facts(educations=[edu(None)])).status == U


def test_work_authorization_is_unknown_never_assumed() -> None:
    result = JUDGE.assess(
        requirement(T.ELIGIBILITY, "Must be authorized to work in the United States"),
        [evidence("Authorized to work in the United States.", 0.9)],
        facts(),
    )
    assert (result.status, result.decisive, result.evidence_ids) == (U, True, [])


def test_graduation_year_and_cgpa() -> None:
    grad = edu(
        DegreeLevel.BACHELOR, end_date=date(2026, 6, 1), gpa=Decimal("8.2"), gpa_scale=Decimal(10)
    )
    cited = uuid.uuid4()
    candidate = facts(educations=[grad], evidence_by_subject={grad.id: [cited]})
    year = requirement(T.ELIGIBILITY, "B.Tech students graduating in 2026")
    assert JUDGE.assess(year, [], candidate).status == M
    assert (
        JUDGE.assess(requirement(T.ELIGIBILITY, "2025 batch graduates only"), [], candidate).status
        == X
    )
    assert (
        JUDGE.assess(requirement(T.ELIGIBILITY, "Minimum CGPA of 7.0"), [], candidate).status == M
    )
    assert (
        JUDGE.assess(requirement(T.ELIGIBILITY, "Minimum CGPA of 8.5/10"), [], candidate).status
        == X
    )
    # A different scale is never converted: can't be assessed.
    four = facts(educations=[edu(DegreeLevel.BACHELOR, gpa=Decimal("3.5"), gpa_scale=Decimal(4))])
    assert JUDGE.assess(requirement(T.ELIGIBILITY, "Minimum CGPA of 7.0/10"), [], four).status == U


# --- Scoring ----------------------------------------------------------------------------


def _pair(
    status: MatchStatus, importance: RequirementImportance = REQ
) -> tuple[JobRequirement, Assessment]:
    req = requirement(T.SKILL, "x", importance)
    return req, Assessment(req.id, status, "", [], None, "rules")


def test_scores_weight_required_over_preferred_and_exclude_unknown() -> None:
    scores = score([_pair(M), _pair(P), _pair(X), _pair(U), _pair(M, PREF), _pair(X, PREF)])
    # required: (1 + 0.5 + 0) / 3; preferred: (1 + 0) / 2; overall: (1.5 + 0.5) / (3 + 1)
    assert scores.required_coverage == 0.5
    assert scores.preferred_coverage == 0.5
    assert scores.overall == 0.5
    only_unknown = score([_pair(U)])
    assert (only_unknown.overall, only_unknown.required_coverage) == (0.0, None)


# --- LLM judge grounding ----------------------------------------------------------------


def _rule(req: JobRequirement, status: MatchStatus = X, **extra: Any) -> Assessment:
    return Assessment(req.id, status, "rule explanation", [], 0.1, "rules", **extra)


def test_llm_cannot_cite_evidence_it_was_not_given() -> None:
    req = requirement(T.SKILL, "Experience with Retrieval-Augmented Generation")
    retrieved = [evidence("Implemented RAG pipeline.", 0.4)]
    raw = {"status": "matched", "explanation": "Invented.", "evidence_ids": [str(uuid.uuid4())]}
    grounded = ground_assessment(raw, req, retrieved, _rule(req), "llm:x")
    assert (grounded.status, grounded.judge) == (X, "rules")  # fell back to the rule result


def test_llm_match_is_kept_when_it_cites_retrieved_evidence() -> None:
    req = requirement(T.SKILL, "Experience with Retrieval-Augmented Generation")
    ev = evidence("Implemented RAG pipeline.", 0.4)
    raw = {
        "status": "matched",
        "explanation": "Your evidence describes a RAG pipeline.",
        "evidence_ids": [str(ev.evidence_id), str(ev.evidence_id)],
    }
    grounded = ground_assessment(raw, req, [ev], _rule(req), "llm:x")
    assert (grounded.status, grounded.evidence_ids, grounded.judge) == (
        M,
        [ev.evidence_id],
        "llm:x",
    )
    assert grounded.explanation == "Your evidence describes a RAG pipeline."


def test_llm_cannot_match_a_technology_the_evidence_doesnt_name() -> None:
    req = requirement(T.TECHNOLOGY, "Kubernetes")
    ev = evidence("Deployed containers to a managed cluster.", 0.3)
    raw = {
        "status": "matched",
        "explanation": "Close enough.",
        "evidence_ids": [str(ev.evidence_id)],
    }
    assert ground_assessment(raw, req, [ev], _rule(req), "llm:x").status == P


def test_llm_cannot_override_structured_checks_or_caps() -> None:
    req = requirement(T.ELIGIBILITY, "Must be authorized to work in the US")
    decisive = _rule(req, U, decisive=True)
    raw = {"status": "matched", "explanation": "Yes.", "evidence_ids": []}
    assert ground_assessment(raw, req, [], decisive, "llm:x") is decisive

    years = requirement(T.EXPERIENCE, "5+ years of ML", min_years=Decimal(5))
    ev = evidence("Built ML systems.", 0.5)
    capped = _rule(
        years, P, cap=P, details={"years_note": "Your dated work experience totals 2 years."}
    )
    raw = {
        "status": "matched",
        "explanation": "Relevant ML work.",
        "evidence_ids": [str(ev.evidence_id)],
    }
    grounded = ground_assessment(raw, years, [ev], capped, "llm:x")
    assert grounded.status == P and grounded.explanation.endswith("totals 2 years.")


def test_llm_prompt_contains_only_retrieved_evidence_and_schema_is_strict() -> None:
    req = requirement(T.EXPERIENCE, "3+ years of Python", min_years=Decimal(3))
    ev = evidence("Built APIs in Python.", 0.5, label="Backend Intern at Acme")
    prompt = build_prompt([(req, [ev], _rule(req, details={"candidate_years": 1.5}))])
    assert str(ev.evidence_id) in prompt and "Backend Intern at Acme" in prompt
    assert "1.5 years" in prompt
    items = JUDGE_SCHEMA["properties"]["assessments"]["items"]
    assert items["additionalProperties"] is False and set(items["required"]) == set(
        items["properties"]
    )
    assert set(items["properties"]["status"]["enum"]) == {
        "matched",
        "partial",
        "missing",
        "unknown",
    }


# --- Regressions found in the end-to-end run --------------------------------------------


def test_a_different_certification_is_never_a_match() -> None:
    req = requirement(
        T.CERTIFICATION, "AWS Certified Machine Learning - Specialty certification is a plus.", PREF
    )
    practitioner = evidence(
        "AWS Certified Cloud Practitioner - Amazon Web Services (2023)",
        0.4,
        label="AWS Certified Cloud Practitioner",
        kind=EvidenceSourceType.CERTIFICATION,
    )
    result = JUDGE.assess(
        req,
        [practitioner],
        facts(certifications={uuid.uuid4(): "AWS Certified Cloud Practitioner"}),
    )
    assert result.status == P and "related certification" in result.explanation
    assert JUDGE.assess(req, [], facts()).status == X


def test_the_held_certification_is_matched_with_its_evidence() -> None:
    cert_id = uuid.uuid4()
    req = requirement(T.CERTIFICATION, "AWS Certified Cloud Practitioner certification required")
    ev = evidence(
        "AWS Certified Cloud Practitioner (2023)",
        0.5,
        kind=EvidenceSourceType.CERTIFICATION,
        record_id=cert_id,
    )
    result = JUDGE.assess(
        req,
        [ev],
        facts(
            certifications={cert_id: "AWS Certified Cloud Practitioner"},
            evidence_by_subject={cert_id: [ev.evidence_id]},
        ),
    )
    assert (result.status, result.evidence_ids) == (M, [ev.evidence_id])


def test_coursework_alone_is_partial_evidence_of_a_technology() -> None:
    course = evidence(
        "Relevant coursework: Machine Learning, Operating Systems",
        0.3,
        kind=EvidenceSourceType.COURSEWORK,
    )
    result = JUDGE.assess(requirement(T.TECHNOLOGY, "Machine Learning"), [course], facts())
    assert result.status == P and "only in your coursework" in result.explanation
    project = evidence("Trained a machine learning model for churn.", 0.3)
    assert (
        JUDGE.assess(
            requirement(T.TECHNOLOGY, "Machine Learning"), [course, project], facts()
        ).status
        == M
    )


def test_absent_technical_skill_is_missing_but_soft_skill_is_unknown() -> None:
    unrelated = [evidence("Built a crawler.", 0.02)]
    assert (
        JUDGE.assess(
            requirement(T.SKILL, "Experience with ONNX or TensorRT."), unrelated, facts()
        ).status
        == X
    )
    assert (
        JUDGE.assess(
            requirement(T.SKILL, "Strong stakeholder communication"), unrelated, facts()
        ).status
        == U
    )
    assert JUDGE.assess(requirement(T.LANGUAGE, "Fluent in German"), unrelated, facts()).status == U
