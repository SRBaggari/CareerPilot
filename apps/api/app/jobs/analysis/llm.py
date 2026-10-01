"""LLM job description analyzer: strict JSON-schema structured output.

The result goes through the same grounding check as the rule-based analyzer, so anything
the model infers rather than reads from the description is discarded.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai.provider import LLMJsonResult, LLMProvider
from app.ai.untrusted import fence, restore, with_rules
from app.jobs.analysis.extracted import ExtractedRequirement, JobExtraction, SalaryInfo
from app.jobs.models import RequirementImportance, RequirementType, SalaryPeriod
from app.profiles.models import EmploymentType, WorkplaceType

PROMPT_VERSION = "job-analysis-v1"
SYSTEM_PROMPT = """You extract structured information from a job description.

Every value is checked against the description and anything not found there is discarded,
so copy text exactly as written and never infer, assume, or complete missing information.

Requirements: one entry per distinct statement. "text" is the statement copied verbatim;
"source_excerpt" is the verbatim sentence or bullet it comes from (often the same).
For category "technology", add one entry per named technology, with "text" set to the
technology's name exactly as written, and "source_excerpt" the statement naming it.

Importance must come from the description itself:
- "required": listed under a requirements/qualifications/eligibility heading, or stated
  with words like must, required, minimum, mandatory, at least.
- "preferred": stated as preferred, nice to have, a plus, bonus, desirable, ideally.
- "informational": everything else - responsibilities, descriptions of the team, company,
  product, or tech stack, benefits, and any statement whose status is not stated.
Never upgrade a statement to "required" without such wording. Responsibilities are always
"informational" with category "responsibility".

Fields such as salary, deadline, location, work mode, and employment type: fill them only
when explicitly stated; otherwise null. For salary, copy the full salary sentence into
salary_text and set salary_currency only when a currency code or unambiguous symbol is
given (a bare "$" is ambiguous: use null). For the deadline, copy the sentence into
deadline_text and give application_deadline as YYYY-MM-DD only if the date is unambiguous."""

_S: dict[str, Any] = {"type": ["string", "null"]}
_N: dict[str, Any] = {"type": ["number", "null"]}


def _enum(values: list[str]) -> dict[str, Any]:
    return {"type": ["string", "null"], "enum": [*values, None]}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}  # fmt: skip


JOB_SCHEMA = _obj({
    "title": _S, "company_name": _S, "location": _S,
    "work_mode": _enum([w.value for w in WorkplaceType]),
    "employment_type": _enum([e.value for e in EmploymentType]),
    "salary_text": _S, "salary_min": _N, "salary_max": _N, "salary_currency": _S,
    "salary_period": _enum([p.value for p in SalaryPeriod]),
    "application_deadline": _S, "deadline_text": _S,
    "requirements": {"type": "array", "items": _obj({
        "category": {"type": "string", "enum": [t.value for t in RequirementType]},
        "importance": {"type": "string", "enum": [i.value for i in RequirementImportance]},
        "text": {"type": "string"},
        "source_excerpt": {"type": "string"},
        "min_years": _N,
    })},
})  # fmt: skip


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def to_extraction(data: dict[str, Any]) -> JobExtraction:
    result = JobExtraction(
        title=data.get("title") or None,
        company_name=data.get("company_name") or None,
        location=data.get("location") or None,
        workplace_type=WorkplaceType(data["work_mode"]) if data.get("work_mode") else None,
        employment_type=(
            EmploymentType(data["employment_type"]) if data.get("employment_type") else None
        ),
        application_deadline=_date(data.get("application_deadline")),
        deadline_text=data.get("deadline_text") or None,
    )
    if data.get("salary_text"):
        result.salary = SalaryInfo(
            text=str(data["salary_text"]),
            minimum=_decimal(data.get("salary_min")),
            maximum=_decimal(data.get("salary_max")),
            currency=str(data["salary_currency"]).upper()[:3]
            if data.get("salary_currency")
            else None,
            period=SalaryPeriod(data["salary_period"]) if data.get("salary_period") else None,
        )
    for item in data.get("requirements") or []:
        try:
            requirement_type = RequirementType(item["category"])
            importance = RequirementImportance(item["importance"])
        except (KeyError, ValueError):
            continue
        if requirement_type == RequirementType.RESPONSIBILITY:
            importance = RequirementImportance.INFORMATIONAL  # duties are never requirements
        text = str(item.get("text") or "").strip()
        excerpt = str(item.get("source_excerpt") or text).strip()
        if text:
            result.requirements.append(ExtractedRequirement(
                requirement_type, importance, text[:2000], excerpt[:2000],
                _decimal(item.get("min_years")),
            ))  # fmt: skip
    return result


class LLMJobAnalyzer:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.name = f"llm:{provider.model}"[:50]

    async def analyze(self, text: str) -> tuple[JobExtraction, LLMJsonResult]:
        result = await self.provider.complete_json(
            system=with_rules(SYSTEM_PROMPT),
            prompt=f"{fence('job_description', text)}\n\nExtract it into the schema.",
            schema=JOB_SCHEMA,
        )
        return to_extraction(restore(result.data)), result
