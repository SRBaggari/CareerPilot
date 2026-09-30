"""Job description analysis on sample job descriptions (no database, no network)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.ai.provider import LLMJsonResult
from app.jobs.analysis.extracted import ExtractedRequirement, JobExtraction, SalaryInfo
from app.jobs.analysis.fields import employment_type, parse_deadline, parse_salary, work_mode
from app.jobs.analysis.grounding import ground
from app.jobs.analysis.heuristic import HeuristicJobAnalyzer, Role, heading_role
from app.jobs.analysis.llm import JOB_SCHEMA, LLMJobAnalyzer, to_extraction
from app.jobs.analysis.vocabulary import find_technologies
from app.jobs.models import RequirementImportance, RequirementType, SalaryPeriod
from app.profiles.models import EmploymentType, WorkplaceType

pytestmark = pytest.mark.anyio
FIXTURES = Path(__file__).parent / "fixtures" / "jobs"
REQ, PREF, INFO = (RequirementImportance.REQUIRED, RequirementImportance.PREFERRED,
                   RequirementImportance.INFORMATIONAL)  # fmt: skip
T = RequirementType


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def sample(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text("utf-8")


def analyze(name: str) -> JobExtraction:
    text = sample(name)
    return ground(HeuristicJobAnalyzer().analyze(text), text)


def rows(
    result: JobExtraction, kind: RequirementType, importance: RequirementImportance
) -> list[str]:
    return [r.description for r in result.requirements
            if r.requirement_type == kind and r.importance == importance]  # fmt: skip


# --- Sample 1: US full-time role with headings --------------------------------------------


def test_us_role_overview() -> None:
    result = analyze("ml_engineer_us")
    assert (result.title, result.company_name, result.location) == (
        "Senior Machine Learning Engineer", "Northwind Robotics", "Austin, TX",
    )  # fmt: skip
    assert result.workplace_type == WorkplaceType.REMOTE
    assert result.employment_type == EmploymentType.FULL_TIME
    assert result.salary is not None
    assert (result.salary.minimum, result.salary.maximum) == (Decimal(160000), Decimal(200000))
    assert result.salary.currency is None  # a bare "$" doesn't say which dollar
    assert result.salary.period == SalaryPeriod.YEAR
    assert result.application_deadline == date(2026, 7, 31)
    assert result.warnings == []


def test_us_role_requirements_are_classified_from_the_text() -> None:
    result = analyze("ml_engineer_us")
    assert rows(result, T.TECHNOLOGY, REQ) == ["Machine Learning", "Python", "SQL", "Docker", "AWS"]
    assert rows(result, T.TECHNOLOGY, PREF) == ["Terraform"]
    # Named only in the company blurb and the duties: informational, not required.
    assert set(rows(result, T.TECHNOLOGY, INFO)) == {"Kubernetes", "Kafka", "PyTorch"}
    [experience] = [r for r in result.requirements if r.requirement_type == T.EXPERIENCE]
    assert (experience.importance, experience.min_years) == (REQ, Decimal(5))
    assert rows(result, T.EDUCATION, REQ) == [
        "Bachelor's degree in Computer Science or a related field."
    ]
    assert rows(result, T.ELIGIBILITY, REQ)[0].startswith("Must be authorized to work")
    assert rows(result, T.CERTIFICATION, PREF) == [
        "AWS Certified Machine Learning - Specialty certification is a plus."
    ]
    assert len(rows(result, T.RESPONSIBILITY, INFO)) == 3
    assert all(
        r.importance == INFO for r in result.requirements if r.requirement_type == T.RESPONSIBILITY
    )


# --- Sample 2: Indian internship with labels ----------------------------------------------


def test_internship_overview() -> None:
    result = analyze("data_intern_india")
    assert (result.title, result.company_name, result.location) == (
        "Data Science Intern", "Pinecrest Analytics Pvt Ltd", "Bengaluru, India",
    )  # fmt: skip
    assert result.workplace_type == WorkplaceType.HYBRID
    assert result.employment_type == EmploymentType.INTERNSHIP
    assert result.salary is not None
    assert (result.salary.minimum, result.salary.currency, result.salary.period) == (
        Decimal(40000), "INR", SalaryPeriod.MONTH,
    )  # fmt: skip
    assert result.application_deadline == date(2026, 8, 15)  # 15/08: unambiguous day/month


def test_internship_eligibility_and_skills() -> None:
    result = analyze("data_intern_india")
    assert rows(result, T.ELIGIBILITY, REQ) == [
        "B.Tech / M.Tech students graduating in 2026.",
        "Minimum CGPA of 7.0 with no active backlogs.",
    ]
    assert rows(result, T.LANGUAGE, REQ) == [
        "Excellent written and verbal communication in English."
    ]
    assert rows(result, T.SKILL, PREF) == ["Knowledge of scikit-learn or TensorFlow."]
    assert set(rows(result, T.TECHNOLOGY, PREF)) == {"scikit-learn", "TensorFlow"}
    # Salary and deadline lines are never mistaken for requirements.
    assert not any("Stipend" in r.description or "Last date" in r.description
                   for r in result.requirements)  # fmt: skip


# --- Sample 3: prose without headings -----------------------------------------------------


def test_prose_job_only_marks_explicit_statements() -> None:
    result = analyze("startup_prose")
    assert (result.title, result.company_name) == ("Backend Developer", "Lumen Health")
    assert result.salary is None and result.application_deadline is None
    assert rows(result, T.SKILL, REQ) == ["Candidates must be comfortable with on-call rotations."]
    assert rows(result, T.SKILL, PREF) == ["Experience with Celery would be nice."]
    # Django/PostgreSQL are described, not demanded: informational only.
    assert set(rows(result, T.TECHNOLOGY, INFO)) == {"Django", "PostgreSQL"}
    assert not rows(result, T.TECHNOLOGY, REQ)


# --- Field parsers --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("CTC: \u20b912-18 LPA", (Decimal(1200000), Decimal(1800000), "INR", SalaryPeriod.YEAR)),
        ("Pay range: USD 60/hr", (Decimal(60), None, "USD", SalaryPeriod.HOUR)),
        ("Base salary \u20ac65k\u201375k", (Decimal(65000), Decimal(75000), "EUR", None)),
        (
            "Compensation: 12 - 18 LPA",
            (Decimal(1200000), Decimal(1800000), "INR", SalaryPeriod.YEAR),
        ),
        ("We raised $50M from investors.", None),
        ("Salary: $90,000 - $80,000", (None, None, None, None)),
    ],
    ids=["lpa", "usd-hourly", "eur-k", "bare-lpa", "funding-not-salary", "contradictory"],
)
def test_parse_salary(line: str, expected: tuple[Any, ...] | None) -> None:
    salary = parse_salary(line)
    got = salary and (salary.minimum, salary.maximum, salary.currency, salary.period)
    assert got == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Apply by July 31, 2026", date(2026, 7, 31)),
        ("Applications close on 5th September 2026", date(2026, 9, 5)),
        ("Application deadline: 2026-07-15", date(2026, 7, 15)),
        ("Last date to apply: 15/08/2026", date(2026, 8, 15)),
        ("Deadline: 05/06/2026", None),  # could be 5 June or May 6
        ("Join our team by July 31, 2026", None),  # not a deadline statement
    ],
)
def test_parse_deadline(line: str, expected: date | None) -> None:
    assert parse_deadline(line)[0] == expected


def test_ambiguous_deadline_is_explained() -> None:
    assert "day/month or month/day" in (parse_deadline("Deadline: 05/06/2026")[2] or "")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("This is a fully remote role", WorkplaceType.REMOTE),
     ("Hybrid (3 days in office)", WorkplaceType.HYBRID),
     ("Remote or hybrid, your choice", None),
     ("We collaborate with remote teams in Berlin", None)],
)  # fmt: skip
def test_work_mode(text: str, expected: WorkplaceType | None) -> None:
    assert work_mode(text)[0] == expected


def test_employment_type_conflicts_are_not_resolved_by_guessing() -> None:
    assert employment_type("Full-time or contract")[0] is None
    assert employment_type("Machine Learning Intern")[0] == EmploymentType.INTERNSHIP


def test_technologies_need_unambiguous_names() -> None:
    assert find_technologies("We go fast with Golang, k8s and Postgres") == [
        "Go", "Kubernetes", "PostgreSQL",
    ]  # fmt: skip
    assert find_technologies("Let's go! JavaScript not Java.") == ["JavaScript", "Java"]


@pytest.mark.parametrize(
    ("line", "role"),
    [("Requirements", Role.REQUIRED), ("What you'll do:", Role.RESPONSIBILITIES),
     ("Nice to have", Role.PREFERRED), ("About Northwind Robotics", Role.ABOUT),
     ("\u2022 Requirements gathering with clients", None)],
)  # fmt: skip
def test_headings(line: str, role: Role | None) -> None:
    assert heading_role(line) == role


# --- LLM analyzer and grounding -----------------------------------------------------------


class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self, data: dict[str, Any]) -> None:
        self.data, self.calls = data, []  # type: ignore[var-annotated]

    async def complete_json(self, *, system: str, prompt: str, schema: dict[str, Any],
                            max_tokens: int = 16000) -> LLMJsonResult:  # fmt: skip
        self.calls.append((system, prompt, schema))
        return LLMJsonResult(self.data, "fake", "fake-model", 120, 80, 9)


EXPERIENCE = "5+ years of professional experience building machine learning systems."


def llm_output(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Senior Machine Learning Engineer", "company_name": "Northwind Robotics",
        "location": "Austin, TX", "work_mode": "remote", "employment_type": "full_time",
        "salary_text": "Salary: $160,000 - $200,000 per year, plus equity.",
        "salary_min": 160000, "salary_max": 200000, "salary_currency": "USD",
        "salary_period": "year", "application_deadline": "2026-07-31",
        "deadline_text": "Apply by July 31, 2026.",
        "requirements": [
            {"category": "technology", "importance": "required", "text": "Python",
             "source_excerpt": "Strong proficiency in Python and SQL.", "min_years": None},
            # Invented: not in the description.
            {"category": "skill", "importance": "required", "text": "Expert in Rust",
             "source_excerpt": "Expert in Rust", "min_years": None},
            # Inferred upgrades: a duty and a nice-to-have marked as required.
            {"category": "skill", "importance": "required", "text": "Mentor junior engineers.",
             "source_excerpt": "Mentor junior engineers.", "min_years": None},
            {"category": "technology", "importance": "required", "text": "Terraform",
             "source_excerpt": "Familiarity with Terraform.", "min_years": None},
            {"category": "experience", "importance": "required", "text": EXPERIENCE,
             "source_excerpt": EXPERIENCE, "min_years": 7},
        ],
    }  # fmt: skip
    return {**base, **overrides}


async def test_llm_analyzer_sends_description_and_strict_schema() -> None:
    provider = FakeProvider(llm_output())
    extraction, result = await LLMJobAnalyzer(provider).analyze(sample("ml_engineer_us"))
    system, prompt, schema = provider.calls[0]
    assert schema is JOB_SCHEMA and "Northwind builds perception software" in prompt
    assert "Never upgrade a statement" in system
    assert result.input_tokens == 120 and extraction.title == "Senior Machine Learning Engineer"


def test_grounding_rejects_inference_from_llm_output() -> None:
    text = sample("ml_engineer_us")
    result = ground(to_extraction(llm_output()), text)
    by_text = {r.description: r for r in result.requirements}
    assert "Expert in Rust" not in by_text  # invented -> dropped
    assert by_text["Mentor junior engineers."].importance == INFO  # a duty, not a requirement
    assert by_text["Terraform"].importance == PREF  # listed under "Nice to have"
    assert by_text["Python"].importance == REQ
    experience = by_text[EXPERIENCE]
    assert experience.min_years is None  # "7" isn't in the text
    assert result.salary is not None and result.salary.currency is None  # "$" isn't "USD"
    assert result.application_deadline == date(2026, 7, 31)
    assert any("discarded" in w for w in result.warnings)
    assert any("less strictly" in w for w in result.warnings)


def test_grounding_rejects_unbacked_fields() -> None:
    text = sample("startup_prose")
    extraction = JobExtraction(
        title="Backend Developer", company_name="Lumen Health Inc.", location="Remote, USA",
        salary=SalaryInfo("Salary: $120k", Decimal(120000), None, "USD", SalaryPeriod.YEAR),
        application_deadline=date(2026, 5, 1), deadline_text="Apply by May 1, 2026",
        requirements=[ExtractedRequirement(T.TECHNOLOGY, REQ, "Kubernetes",
                                           "You will work on our Django services")],
    )  # fmt: skip
    result = ground(extraction, text)
    assert result.title == "Backend Developer"
    assert result.company_name is None and result.location is None
    assert result.salary is None and result.application_deadline is None
    assert result.requirements == []


def test_llm_schema_is_strict() -> None:
    def check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    check(JOB_SCHEMA)
    categories = JOB_SCHEMA["properties"]["requirements"]["items"]["properties"]
    assert set(categories["importance"]["enum"]) == {"required", "preferred", "informational"}


def test_llm_responsibilities_are_always_informational() -> None:
    data = llm_output(requirements=[{
        "category": "responsibility", "importance": "required", "text": "Mentor junior engineers.",
        "source_excerpt": "Mentor junior engineers.", "min_years": None,
    }])  # fmt: skip
    [requirement] = to_extraction(data).requirements
    assert requirement.importance == INFO
