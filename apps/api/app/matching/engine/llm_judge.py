"""LLM requirement judge (structured output), grounded against the retrieved evidence.

The model sees each requirement with only the verified evidence retrieval returned for it,
and returns a status, explanation, and cited evidence IDs. Its answer is then checked:
cited IDs must be ones it was given, MATCHED/PARTIAL must cite evidence, a technology can
only be MATCHED by evidence that names it, and structured checks (degree, eligibility,
years) cannot be overridden.
"""

from typing import Any

from app.ai.provider import LLMJsonResult, LLMProvider
from app.ai.untrusted import safe_json, with_rules
from app.jobs.analysis.vocabulary import find_technologies
from app.jobs.models import JobRequirement, RequirementType
from app.matching.engine.judge import STATUS_RANK, Assessment, evidence_text
from app.matching.models import MatchStatus
from app.retrieval.schemas import RetrievedEvidence

PROMPT_VERSION = "match-judge-v1"
SYSTEM_PROMPT = """You assess how well a candidate's verified evidence covers each job requirement.

For every requirement you receive the candidate evidence retrieved for it (each with an ID).
Judge ONLY from that evidence; never assume skills, experience, or facts it doesn't state.

Statuses:
- "matched": the evidence clearly shows the requirement is met.
- "partial": related evidence, but it doesn't fully meet the requirement (narrower scope,
  a related but different technology, fewer years).
- "missing": the evidence shows nothing that meets it.
- "unknown": it can't be judged from evidence (e.g. work authorization, soft skills that
  the evidence doesn't touch on).

Rules:
- evidence_ids must be IDs from that requirement's list; cite every piece you rely on.
- "matched" and "partial" require at least one cited evidence ID.
- The explanation is one or two plain sentences addressed to the candidate ("Your evidence
  describes ..."), naming what the evidence shows. Do not quote text that isn't in it.
- A profile note, when given, is a checked fact about the candidate; respect it."""

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_id": {"type": "string"},
                    "status": {"type": "string", "enum": [s.value for s in MatchStatus]},
                    "explanation": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["requirement_id", "status", "explanation", "evidence_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assessments"],
    "additionalProperties": False,
}

JudgeItem = tuple[JobRequirement, list[RetrievedEvidence], Assessment]


def build_prompt(items: list[JudgeItem]) -> str:
    requirements = []
    for requirement, retrieved, rule in items:
        entry: dict[str, Any] = {
            "requirement_id": str(requirement.id),
            "type": requirement.requirement_type.value,
            "importance": requirement.importance.value,
            "requirement": requirement.description,
            "evidence": [
                {"evidence_id": str(e.evidence_id), "text": evidence_text(e).strip()}
                for e in retrieved
            ],
        }
        if "candidate_years" in rule.details:
            entry["profile_note"] = (
                f"Dated work experience totals {rule.details['candidate_years']} years."
                if rule.details["candidate_years"] is not None
                else "The candidate's work experience has no dates."
            )
        requirements.append(entry)
    return (
        "<requirements>\n" + safe_json(requirements, indent=1) + "\n</requirements>\n\n"
        "Assess every requirement."
    )


def _cap(status: MatchStatus, cap: MatchStatus | None) -> MatchStatus:
    if cap is None or status == MatchStatus.UNKNOWN:
        return status
    return status if STATUS_RANK[status] <= STATUS_RANK[cap] else cap


def ground_assessment(
    raw: dict[str, Any],
    requirement: JobRequirement,
    retrieved: list[RetrievedEvidence],
    rule: Assessment,
    judge_name: str,
) -> Assessment:
    """Turn one model assessment into a grounded Assessment (or keep the rule result)."""
    if rule.decisive:
        return rule  # degree/eligibility/GPA checks are facts, not opinions
    try:
        status = MatchStatus(raw["status"])
    except (KeyError, ValueError):
        return rule
    allowed = {str(e.evidence_id): e for e in retrieved}
    cited = [allowed[i] for i in dict.fromkeys(raw.get("evidence_ids") or []) if i in allowed]
    if status in (MatchStatus.MATCHED, MatchStatus.PARTIAL) and not cited:
        return rule  # an unsupported positive judgement: keep the rule-based result
    if requirement.requirement_type == RequirementType.TECHNOLOGY and status == MatchStatus.MATCHED:
        concept = (find_technologies(requirement.description) or [requirement.description])[0]
        if not any(concept in find_technologies(evidence_text(e)) for e in cited):
            status = MatchStatus.PARTIAL  # related evidence that doesn't name the technology
    status = _cap(status, rule.cap)
    explanation = str(raw.get("explanation") or "").strip()[:600] or rule.explanation
    if note := rule.details.get("years_note"):
        explanation = f"{explanation} {note}"
    return Assessment(
        requirement_id=requirement.id,
        status=status,
        explanation=explanation,
        evidence_ids=[e.evidence_id for e in cited] if status != MatchStatus.MISSING else [],
        semantic_similarity=max((e.similarity for e in cited), default=rule.semantic_similarity),
        judge=judge_name,
        basis="evidence" if cited else "none",
        cap=rule.cap,
        details=rule.details,
    )


class LLMJudge:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.name = f"llm:{provider.model}"[:50]

    async def assess(self, items: list[JudgeItem]) -> tuple[list[Assessment], LLMJsonResult]:
        result = await self.provider.complete_json(
            system=with_rules(SYSTEM_PROMPT), prompt=build_prompt(items), schema=JUDGE_SCHEMA
        )
        by_id: dict[str, dict[str, Any]] = {}
        for raw in result.data.get("assessments") or []:
            if isinstance(raw, dict) and isinstance(raw.get("requirement_id"), str):
                by_id.setdefault(raw["requirement_id"], raw)
        assessments = []
        for requirement, retrieved, rule in items:
            raw = by_id.get(str(requirement.id))
            assessments.append(
                ground_assessment(raw, requirement, retrieved, rule, self.name) if raw else rule
            )
        return assessments, result
