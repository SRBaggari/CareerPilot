"""Recommendations without a database: requirement analysis of a posting, eligibility
filtering, preference fit, and explanations that say why."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.discovery.models import NormalizedJob, WorkMode
from app.jobs.models import JobRequirement, RequirementImportance, RequirementType
from app.matching.engine.facts import CandidateFacts
from app.matching.engine.judge import Assessment
from app.matching.engine.scoring import score
from app.matching.models import MatchStatus
from app.profiles.models import CandidateProfile, EmploymentType, ExperienceLevel, WorkplaceType
from app.recommendations.pipeline import (
    Evaluation,
    eligibility,
    explain,
    preference_fit,
    requirements_of,
)
from app.retrieval.schemas import RetrievedEvidence

R, P_ = RequirementImportance.REQUIRED, RequirementImportance.PREFERRED
T = RequirementType
M, P, X, U = MatchStatus.MATCHED, MatchStatus.PARTIAL, MatchStatus.MISSING, MatchStatus.UNKNOWN
TODAY = date(2026, 9, 30)
RAG_EVIDENCE, DOCKER_EVIDENCE = uuid.uuid4(), uuid.uuid4()


def posting(**overrides: Any) -> NormalizedJob:
    base: dict[str, Any] = {
        "source": "mock",
        "source_identifier": "1",
        "title": "ML Engineer",
        "company": "Northwind",
        "location": "Hyderabad, India",
        "description": "…",
        "employment_type": EmploymentType.FULL_TIME,
        "work_mode": WorkMode.REMOTE,
        "posted_date": date(2026, 9, 20),
        "experience_level": ExperienceLevel.ENTRY_LEVEL,
    }
    return NormalizedJob(**{**base, **overrides})


def profile(**prefs: Any) -> CandidateProfile:
    return CandidateProfile(
        id=uuid.uuid4(),
        full_name="Test Candidate",
        preferred_roles=prefs.get("roles", []),
        preferred_locations=prefs.get("locations", []),
        work_modes=prefs.get("modes", []),
        job_types=prefs.get("types", []),
        experience_level=prefs.get("level"),
    )


def retrieved(evidence_id: uuid.UUID, text: str, kind: str, label: str) -> RetrievedEvidence:
    return RetrievedEvidence.model_validate(
        {
            "evidence_id": evidence_id,
            "candidate_id": uuid.uuid4(),
            "evidence_type": kind,
            "factual_content": text,
            "similarity": 0.8,
            "confidence": "high",
            "verification_status": "verified",
            "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-01T00:00:00Z",
            "source": {
                "record_type": kind,
                "record_id": str(uuid.uuid4()),
                "record_label": label,
                "origin": "user_entered",
                "resume_id": None,
                "resume_file_name": None,
            },
        }
    )


Item = tuple[str, RequirementType, RequirementImportance, MatchStatus, list[uuid.UUID]]


def evaluation(
    items: list[Item],
    job: NormalizedJob | None = None,
    years: dict[str, Decimal] | None = None,
) -> Evaluation:
    pairs = []
    for description, kind, importance, status, cited in items:
        requirement = JobRequirement(
            id=uuid.uuid4(),
            requirement_type=kind,
            importance=importance,
            description=description,
            min_years=(years or {}).get(description),
        )
        explanation = f"Explanation for {description}."
        pairs.append(
            (requirement, Assessment(requirement.id, status, explanation, cited, 0.8, "rules"))
        )
    ev = Evaluation(job or posting(), pairs)
    ev.retrieved = {
        RAG_EVIDENCE: retrieved(
            RAG_EVIDENCE, "Implemented RAG pipeline.", "project", "Multi-Agent Research Assistant"
        ),
        DOCKER_EVIDENCE: retrieved(
            DOCKER_EVIDENCE,
            "Deployed ML models with Docker on AWS.",
            "work_experience",
            "ML Intern at Acme",
        ),
    }
    ev.scores = score(pairs)
    return ev


FACTS = CandidateFacts(profile_id=uuid.uuid4(), experience_years=1.0)
TYPICAL = [
    ("Python", T.TECHNOLOGY, R, M, [DOCKER_EVIDENCE]),
    ("Docker", T.TECHNOLOGY, R, M, [DOCKER_EVIDENCE]),
    ("Kubernetes", T.TECHNOLOGY, R, X, []),
    ("RAG", T.TECHNOLOGY, P_, M, [RAG_EVIDENCE]),
    ("Terraform", T.TECHNOLOGY, P_, X, []),
]


# --- Analysis ---------------------------------------------------------------------------------


def test_a_postings_requirements_come_from_its_text() -> None:
    job = posting(
        description="Requirements:\n- Experience with Python and SQL.\n- 3+ years of "
        "experience building ML systems.\n\nNice to have:\n- Experience with Docker."
    )
    found = {(r.description, r.importance) for r in requirements_of(job)}
    assert ("Experience with Python and SQL.", R) in found
    assert ("Experience with Docker.", P_) in found
    assert any(r.min_years == 3 for r in requirements_of(job))


# --- Eligibility ------------------------------------------------------------------------------


def test_a_good_fit_passes_with_no_exclusions() -> None:
    exclusions, concerns = eligibility(evaluation(TYPICAL), profile(), FACTS, TODAY)
    assert exclusions == [] and concerns == []


@pytest.mark.parametrize(
    ("job", "prefs", "reason"),
    [
        (posting(deadline=date(2026, 9, 1)), {}, "deadline (2026-09-01) has passed"),
        (
            posting(employment_type=EmploymentType.INTERNSHIP),
            {"types": [EmploymentType.FULL_TIME]},
            "It's a internship role; you prefer full-time.",
        ),
        (
            posting(work_mode=WorkMode.ONSITE),
            {"modes": [WorkplaceType.REMOTE]},
            "It's on-site; you prefer remote work.",
        ),
    ],
)
def test_ineligible_jobs_are_filtered_out_with_reasons(
    job: NormalizedJob, prefs: dict[str, Any], reason: str
) -> None:
    exclusions, _ = eligibility(evaluation(TYPICAL, job), profile(**prefs), FACTS, TODAY)
    assert any(reason in e for e in exclusions), exclusions


def test_far_more_years_than_the_work_history_filters_a_job_out() -> None:
    items = [*TYPICAL, ("5+ years of experience.", T.EXPERIENCE, R, P, [])]
    ev = evaluation(items, years={"5+ years of experience.": Decimal(5)})
    exclusions, _ = eligibility(ev, profile(), FACTS, TODAY)
    assert "Requires 5+ years of experience; your work history adds up to about 1.0." in exclusions


def test_a_small_years_gap_is_a_concern_not_an_exclusion() -> None:
    items = [*TYPICAL, ("2+ years of experience.", T.EXPERIENCE, R, P, [])]
    ev = evaluation(items, years={"2+ years of experience.": Decimal(2)})
    exclusions, concerns = eligibility(ev, profile(), FACTS, TODAY)
    assert exclusions == [] and concerns == [
        "2+ years of experience. Explanation for 2+ years of experience.."
    ]


def test_no_covered_required_requirement_filters_a_job_out() -> None:
    items: list[Item] = [("Go", T.TECHNOLOGY, R, X, []), ("Rust", T.TECHNOLOGY, R, X, [])]
    exclusions, _ = eligibility(evaluation(items), profile(), FACTS, TODAY)
    assert "doesn't cover any of the job's required requirements" in exclusions[0]


def test_a_posting_without_requirements_cannot_be_explained() -> None:
    exclusions, _ = eligibility(evaluation([]), profile(), FACTS, TODAY)
    assert "states no requirements" in exclusions[0]


def test_eligibility_concerns() -> None:
    items = [
        *TYPICAL,
        ("Must be authorized to work in the US.", T.ELIGIBILITY, R, U, []),
        ("AWS Certified Solutions Architect.", T.CERTIFICATION, R, P, []),
        ("Master's degree in Computer Science.", T.EDUCATION, R, X, []),
    ]
    job = posting(deadline=date(2026, 10, 3), work_mode=WorkMode.ONSITE, location="Pune, India")
    _, concerns = eligibility(
        evaluation(items, job), profile(locations=["Hyderabad"]), FACTS, TODAY
    )
    joined = " | ".join(concerns)
    for expected in (
        "Apply by 2026-10-03: 3 days left.",
        "Pune, India isn't one of your preferred",
        "authorized to work",
        "AWS Certified",
        "Master's degree",
    ):
        assert expected in joined, joined


# --- Explanations -----------------------------------------------------------------------------


def test_the_explanation_says_why() -> None:
    prefs = profile(
        roles=["ML Engineer"],
        modes=[WorkplaceType.REMOTE],
        types=[EmploymentType.FULL_TIME],
        level=ExperienceLevel.ENTRY_LEVEL,
    )
    explanation = explain(evaluation(TYPICAL), prefs)
    assert explanation.summary == (
        "Recommended because your evidence covers 2 of 3 required requirements, including "
        "Python, Docker and RAG; matches your preferred role “ML Engineer”."
    )
    assert (explanation.required_met, explanation.required_total) == (2, 3)
    assert [s.skill for s in explanation.matched_skills] == ["Python", "Docker", "RAG"]
    assert [s.skill for s in explanation.missing_skills] == ["Kubernetes", "Terraform"]
    assert explanation.reasons[0] == (
        "Your verified evidence covers 2 of 3 required requirements: Python and Docker."
    )
    assert "Also meets preferred requirements: RAG." in explanation.reasons
    assert explanation.preference_fit == [
        "Matches your preferred role “ML Engineer”.",
        "Remote, as you prefer.",
        "A full-time role, as you prefer.",
        "At your experience level (entry level).",
    ]
    [project] = explanation.relevant_projects
    assert (project.title, project.evidence, project.supports) == (
        "Multi-Agent Research Assistant",
        ["Implemented RAG pipeline."],
        ["RAG"],
    )
    assert "Your Multi-Agent Research Assistant project is relevant: it covers RAG." in (
        explanation.reasons
    )
    assert len(explanation.requirements) == 5  # every result, not just a number


def test_preferences_that_do_not_match_are_not_claimed() -> None:
    job = posting(title="Data Analyst", work_mode=WorkMode.ONSITE)
    prefs = profile(roles=["ML Engineer"], modes=[WorkplaceType.REMOTE])
    assert preference_fit(job, prefs) == []


def test_skills_are_named_once_and_statements_read_cleanly() -> None:
    items: list[Item] = [
        ("Experience with Python and SQL.", T.SKILL, R, M, [DOCKER_EVIDENCE]),
        ("Python", T.TECHNOLOGY, R, M, [DOCKER_EVIDENCE]),
        ("Strong written communication.", T.SKILL, P_, M, [DOCKER_EVIDENCE]),
    ]
    explanation = explain(evaluation(items), profile())
    assert [s.skill for s in explanation.matched_skills] == [
        "Python",
        "SQL",
        "Strong written communication",
    ]
    assert explanation.reasons[0] == (
        "Your verified evidence covers 2 of 2 required requirements: Experience with Python "
        "and SQL and Python."
    )
    assert ".." not in " ".join(explanation.reasons)


def test_preference_wording() -> None:
    job = posting(employment_type=EmploymentType.INTERNSHIP, work_mode=WorkMode.REMOTE)
    prefs = profile(types=[EmploymentType.INTERNSHIP], locations=["Hyderabad"])
    assert preference_fit(job, prefs) == [
        "An internship role, as you prefer.",
        "Remote, so it works from your preferred locations.",
    ]
