"""Grounding: keep only what the job description explicitly states.

Applied to every analyzer's output (it is the guard against a language model inferring or
inventing requirements). Text values must appear in the description; numbers, dates, and
currencies must be backed by the verbatim text they were read from.
"""

import re

from app.jobs.analysis.extracted import (
    IMPORTANCE_RANK,
    ExtractedRequirement,
    JobExtraction,
    SalaryInfo,
)
from app.jobs.analysis.fields import parse_deadline
from app.jobs.analysis.heuristic import PREFERRED_CUE, Role, split_sections
from app.jobs.analysis.vocabulary import mentions
from app.jobs.models import RequirementImportance, RequirementType
from app.resumes.grounding import Grounder, normalize

_CURRENCY_MARKERS = {
    "USD": ("usd", "us$"), "EUR": ("eur", "€"), "GBP": ("gbp", "£"),
    "INR": ("inr", "₹", "rs", "lpa", "lakh"), "CAD": ("cad",), "AUD": ("aud",),
    "SGD": ("sgd",),
}  # fmt: skip


def _digits_present(value: object, text: str) -> bool:
    number = str(value)
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    return number in text.replace(",", "")


def _ground_salary(salary: SalaryInfo, grounder: Grounder, text: str) -> SalaryInfo | None:
    if not grounder.contains(salary.text):
        return None
    excerpt = salary.text.lower()
    compact = excerpt.replace(",", "")
    lakh = "lpa" in excerpt or "lakh" in excerpt
    for field in ("minimum", "maximum"):
        value = getattr(salary, field)
        # Amounts given in thousands/lakhs ("160k", "12 LPA") are compared by their stated form.
        if value is not None and not (
            _digits_present(value, compact)
            or (value % 1000 == 0 and _digits_present(value / 1000, compact))
            or (lakh and value % 100_000 == 0 and _digits_present(value / 100_000, compact))
        ):
            setattr(salary, field, None)
    markers = _CURRENCY_MARKERS.get(salary.currency or "", ())
    if salary.currency and not any(marker in excerpt for marker in markers):
        salary.currency = None  # e.g. a bare "$" does not say which dollar
    if salary.period is not None and not re.search(
        rf"{salary.period.value[:2]}|annum|lpa|/\s?(hr|yr|mo)", excerpt
    ):
        salary.period = None
    return salary


def ground(extraction: JobExtraction, text: str) -> JobExtraction:
    grounder = Grounder(text)
    dropped = 0
    result = JobExtraction(
        workplace_type=extraction.workplace_type,
        employment_type=extraction.employment_type,
        warnings=list(extraction.warnings),
    )
    for field in ("title", "company_name", "location"):
        value = getattr(extraction, field)
        if value and grounder.contains(value):
            setattr(result, field, value)
        elif value:
            dropped += 1

    if extraction.salary is not None:
        result.salary = _ground_salary(extraction.salary, grounder, text)
        dropped += result.salary is None

    if extraction.application_deadline is not None:
        excerpt = extraction.deadline_text or ""
        parsed, _, _ = parse_deadline(excerpt) if excerpt else (None, None, None)
        backed = grounder.contains(excerpt) and (
            parsed == extraction.application_deadline
            or (parsed is None and str(extraction.application_deadline.year) in excerpt)
        )
        if backed:
            result.application_deadline = extraction.application_deadline
            result.deadline_text = excerpt
        else:
            dropped += 1

    sections = [(role, normalize(" ".join(lines))) for role, lines in split_sections(text)]
    capped = 0
    seen: set[tuple[RequirementType, str]] = set()
    for requirement in extraction.requirements:
        if not _requirement_ok(requirement, grounder):
            dropped += 1
            continue
        ceiling = _importance_ceiling(requirement.source_excerpt, sections)
        if IMPORTANCE_RANK[requirement.importance] > IMPORTANCE_RANK[ceiling]:
            requirement.importance = ceiling
            capped += 1
        key = (requirement.requirement_type, " ".join(requirement.description.lower().split()))
        if key in seen:
            continue
        seen.add(key)
        if requirement.min_years is not None and not _digits_present(
            requirement.min_years, requirement.source_excerpt
        ):
            requirement.min_years = None
        result.requirements.append(requirement)

    if capped:
        result.warnings.append(
            f"{capped} requirement(s) were marked less strictly because the job description "
            "doesn't state them as required."
        )
    if dropped:
        result.warnings.append(
            f"{dropped} extracted value(s) were discarded because they could not be found in "
            "the job description."
        )
    return result


_DESCRIPTIVE = (Role.RESPONSIBILITIES, Role.ABOUT, Role.TECH, Role.BENEFITS)


def _importance_ceiling(excerpt: str, sections: list[tuple[Role, str]]) -> RequirementImportance:
    """The strongest importance the description's own structure and wording support."""
    needle = normalize(excerpt)
    role = next((r for r, body in sections if needle and needle in body), None)
    if role in _DESCRIPTIVE:
        return RequirementImportance.INFORMATIONAL  # duties and descriptions aren't requirements
    if role == Role.PREFERRED or PREFERRED_CUE.search(excerpt):
        return RequirementImportance.PREFERRED  # "Nice to have" section or "... is a plus"
    return RequirementImportance.REQUIRED  # unknown sections: nothing to contradict the model


def _requirement_ok(requirement: ExtractedRequirement, grounder: Grounder) -> bool:
    if not grounder.contains(requirement.source_excerpt):
        return False
    if requirement.requirement_type == RequirementType.TECHNOLOGY:
        # The technology must be named in its own excerpt (aliases such as "k8s" count).
        return mentions(requirement.source_excerpt, requirement.description)
    return grounder.contains(requirement.description)
