"""Optional LLM reviewer (structured output) for claims the rules can't settle on wording.

The model sees each claim with only the candidate evidence offered for it and returns a
status, reason and cited evidence IDs. The engine, not the model, decides what to do with
the answer: a stricter status is always accepted; an upgrade to SUPPORTED is accepted only
when the model cites offered evidence *and* that evidence passes every hard rule check.
"""

from dataclasses import dataclass
from typing import Any

from app.ai.provider import LLMJsonResult, LLMProvider
from app.ai.untrusted import safe_json, with_rules
from app.documents.models import VerificationVerdict

PROMPT_VERSION = "claim-verification-v1"
SYSTEM_PROMPT = """You are a strict fact-checker for a job candidate's application documents.

For each claim you receive the candidate's own verified evidence offered for it (each with
an ID) and the result of automatic rule checks. Decide whether the evidence supports the
claim. Judge ONLY from the evidence given; never assume anything it doesn't state.

Statuses:
- "supported": the evidence directly states what the claim says. Rewording is fine
  ("RAG" for "retrieval-augmented generation"); new facts are not.
- "partially_supported": the evidence supports part of the claim but not all of it.
- "unsupported": the evidence doesn't support the claim.
- "contradicted": the evidence conflicts with the claim (different numbers, dates, roles).

Be strict. Any number, metric, scale, employer, product, certification, technology,
qualifier ("production", "scalable") or responsibility ("led", "managed") that the
evidence doesn't state makes the claim at best partially supported.

evidence_ids must be IDs from that claim's list: cite the evidence you relied on. A
"supported" verdict requires at least one. The reason is one plain sentence."""

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "status": {"type": "string", "enum": [v.value for v in VerificationVerdict]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["claim_id", "status", "evidence_ids", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ReviewItem:
    claim_id: str
    text: str
    claim_type: str
    evidence: list[tuple[str, str]]  # (evidence id, text)
    rule_status: VerificationVerdict
    rule_reason: str


@dataclass(frozen=True)
class Review:
    status: VerificationVerdict
    evidence_ids: list[str]
    reason: str


def build_prompt(items: list[ReviewItem]) -> str:
    claims = [
        {
            "claim_id": item.claim_id,
            "claim": item.text,
            "type": item.claim_type,
            "evidence": [{"evidence_id": i, "text": t} for i, t in item.evidence],
            "rule_check": {"status": item.rule_status.value, "reason": item.rule_reason},
        }
        for item in items
    ]
    body = safe_json(claims, indent=1)
    return f"<claims>\n{body}\n</claims>\n\nVerify every claim."


class LLMReviewer:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.name = f"llm:{provider.model}"[:50]

    async def review(self, items: list[ReviewItem]) -> tuple[dict[str, Review], LLMJsonResult]:
        result = await self.provider.complete_json(
            system=with_rules(SYSTEM_PROMPT), prompt=build_prompt(items), schema=REVIEW_SCHEMA
        )
        reviews: dict[str, Review] = {}
        for entry in result.data.get("results") or []:
            try:
                status = VerificationVerdict(entry.get("status"))
            except ValueError:
                continue  # an invalid status is ignored: the rule result stands
            reviews[str(entry.get("claim_id"))] = Review(
                status=status,
                evidence_ids=[str(i) for i in entry.get("evidence_ids") or []],
                reason=str(entry.get("reason") or "").strip()[:500],
            )
        return reviews, result
