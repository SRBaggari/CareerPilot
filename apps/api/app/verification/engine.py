"""The verification engine: claims -> evidence retrieval -> comparison -> verdict.

    Generated document
    -> claim extraction          (extraction.py)
    -> evidence retrieval        cited evidence + the closest verified evidence in scope
    -> evidence comparison       rules (compare.py) and the stored profile (knowledge.py)
    -> verification              SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONTRADICTED
    -> approved / rejected       only SUPPORTED is approved

Guarantees:
- Only the candidate's own *verified* evidence can support a claim; citing anything else
  is reported, never used.
- A hard factual failure (veto) can't be overruled, not even by the LLM reviewer.
- A verdict only becomes SUPPORTED when evidence supports it, and the result always says
  which evidence and by which method (``rule_based`` or ``llm``). Nothing is upgraded
  silently.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.models import AIExecutionLog, AIExecutionStatus, AIOperation
from app.ai.provider import LLMError, LLMProvider
from app.core.config import Settings
from app.documents.models import VerificationVerdict
from app.retrieval.service import retrieve_verified_for_queries
from app.users.models import User
from app.verification.compare import (
    PARTIAL_COVERAGE,
    ClaimKind,
    Comparison,
    compare,
    is_non_factual,
)
from app.verification.knowledge import (
    CandidateKnowledge,
    Evidence,
    check_contradictions,
    check_record_fact,
)
from app.verification.llm import PROMPT_VERSION, LLMReviewer, Review, ReviewItem
from app.verification.types import (
    ClaimInput,
    ClaimResult,
    ClaimType,
    EvidenceSource,
    VerificationReportOut,
    build_report,
)

V = VerificationVerdict
RETRIEVE_TOP_K = 5
MAX_RETRIEVED = 3  # retrieved evidence items compared per claim
LLM_UPGRADE_CONFIDENCE = 0.75
# The reviewer may only upgrade a claim the cited evidence already partly supports by the
# rules: it may judge a paraphrase faithful, not supply facts the evidence lacks.
LLM_MIN_COVERAGE = PARTIAL_COVERAGE
# How to pick among comparisons against different evidence: support wins; a conflict with
# stored evidence is reported before a mere lack of support.
_PICK = {V.SUPPORTED: 3, V.CONTRADICTED: 2, V.PARTIALLY_SUPPORTED: 1, V.UNSUPPORTED: 0}
# How strict a verdict is (for "the reviewer may always make a verdict stricter").
STRICTNESS = {V.SUPPORTED: 0, V.PARTIALLY_SUPPORTED: 1, V.UNSUPPORTED: 2, V.CONTRADICTED: 3}


@dataclass
class Assessment:
    result: ClaimResult
    veto: bool  # a hard failure: the LLM reviewer can't upgrade it
    offered: dict[uuid.UUID, Evidence]  # the evidence a reviewer may cite


def _kind(claim: ClaimInput) -> ClaimKind:
    return ClaimKind.SKILL if claim.claim_type == ClaimType.SKILL else ClaimKind.STATEMENT


def _result(
    claim: ClaimInput,
    status: VerificationVerdict,
    reason: str,
    *,
    confidence: float,
    evidence_ids: Sequence[uuid.UUID] = (),
    source: EvidenceSource = "none",
    method: str = "rule_based",
) -> ClaimResult:
    return ClaimResult(
        claim_text=claim.text,
        claim_type=claim.claim_type,
        section=claim.section,
        position=claim.position,
        record_id=claim.record_id,
        cited_evidence_ids=list(dict.fromkeys(claim.cited_evidence_ids)),
        evidence_ids=list(evidence_ids),
        evidence_source=source,
        verification_status=status,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        reason=reason,
        method=method,
    )


def _citation_notes(claim: ClaimInput, knowledge: CandidateKnowledge) -> list[str]:
    scope = knowledge.scope(claim)
    notes = []
    for evidence_id in dict.fromkeys(claim.cited_evidence_ids):
        if evidence_id in scope:
            continue
        if evidence_id in knowledge.unverified:
            notes.append("Cites evidence you haven't confirmed, which can't support a claim.")
        elif evidence_id in knowledge.verified:
            owner = knowledge.verified[evidence_id].context or "another item"
            notes.append(f"Cites evidence that belongs to {owner}, not to this item.")
        else:
            notes.append("Cites evidence that isn't one of your evidence items.")
    return list(dict.fromkeys(notes))


def assess(
    claim: ClaimInput, knowledge: CandidateKnowledge, retrieved: Sequence[uuid.UUID] = ()
) -> Assessment:
    """Verify one claim with the rules and the stored profile (no LLM)."""
    if claim.is_record_fact:
        check = check_record_fact(claim, knowledge)
        supported = check.verdict == V.SUPPORTED
        result = _result(claim, check.verdict, check.reason, confidence=1.0 if supported else 0.0,
                         source="profile")  # fmt: skip
        return Assessment(result, veto=True, offered={})

    if claim.claim_type in (ClaimType.LETTER, ClaimType.ANSWER) and is_non_factual(
        claim.text, set((claim.facts or {}).get("allowed_names", []))
    ):
        result = _result(
            claim, V.SUPPORTED, "Not a factual claim about you: a greeting, statement of "
            "interest or courtesy.", confidence=1.0,
        )  # fmt: skip
        return Assessment(result, veto=True, offered={})

    scope = knowledge.scope(claim)
    notes = _citation_notes(claim, knowledge)
    prefix = " ".join(notes) + " " if notes else ""
    cited = [i for i in dict.fromkeys(claim.cited_evidence_ids) if i in scope]
    extra = [i for i in dict.fromkeys(retrieved) if i in scope and i not in cited][:MAX_RETRIEVED]
    offered = {i: scope[i] for i in [*cited, *extra]}

    conflict = check_contradictions(claim, knowledge)
    if conflict is not None:
        result = _result(claim, conflict.verdict, prefix + conflict.reason, confidence=0.0,
                         source="profile")  # fmt: skip
        return Assessment(result, veto=True, offered=offered)

    kind = _kind(claim)
    options: list[tuple[EvidenceSource, list[uuid.UUID], Comparison]] = []
    if cited:
        options.append(("cited", cited, compare(claim.text, [scope[i].text for i in cited], kind)))
    if not (options and options[0][2].supported):
        options += [("retrieved", [i], compare(claim.text, [scope[i].text], kind)) for i in extra]
        if len(extra) > 1:
            texts = [scope[i].text for i in extra]
            options.append(("retrieved", extra, compare(claim.text, texts, kind)))
    if not options:
        found = compare(claim.text, [], kind)
        result = _result(claim, found.verdict, prefix + found.reason, confidence=0.0)
        return Assessment(result, veto=True, offered=offered)

    source, ids, found = max(
        options, key=lambda o: (_PICK[o[2].verdict], o[2].confidence, o[0] == "cited")
    )
    reason = found.reason
    confidence = found.confidence
    if source == "retrieved" and found.supported:
        reason = "Supported by your evidence, though not the evidence it cited."
        confidence *= 0.9
    result = _result(claim, found.verdict, prefix + reason, confidence=confidence,
                     evidence_ids=ids, source=source)  # fmt: skip
    # Upgrades need every compared evidence set to be free of hard failures.
    veto = found.veto or any(o[2].veto for o in options if o[2].verdict != V.SUPPORTED)
    return Assessment(result, veto=veto, offered=offered)


def apply_review(
    assessment: Assessment, review: Review | None, knowledge: CandidateKnowledge, model: str
) -> ClaimResult:
    """Merge the LLM reviewer's opinion: stricter always wins; upgrades must be earned."""
    rules = assessment.result
    if review is None:
        return rules
    offered_ids = {str(i): i for i in assessment.offered}
    cited = [offered_ids[i] for i in dict.fromkeys(review.evidence_ids) if i in offered_ids]
    label = f"AI reviewer ({model})"

    if STRICTNESS[review.status] > STRICTNESS[rules.verification_status]:
        return rules.model_copy(update={
            "verification_status": review.status, "method": "llm",
            "reason": f"{label}: {review.reason}", "confidence": min(rules.confidence, 0.5),
            "evidence_ids": cited or rules.evidence_ids,
        })  # fmt: skip
    if review.status == V.SUPPORTED and rules.verification_status != V.SUPPORTED:
        claim_text, kind = (
            rules.claim_text,
            (ClaimKind.SKILL if rules.claim_type == ClaimType.SKILL else ClaimKind.STATEMENT),
        )
        recheck = compare(claim_text, [assessment.offered[i].text for i in cited], kind)
        if (
            cited
            and not assessment.veto
            and not recheck.veto
            and recheck.confidence >= LLM_MIN_COVERAGE
        ):
            return rules.model_copy(update={
                "verification_status": V.SUPPORTED, "method": "llm", "evidence_ids": cited,
                "evidence_source": "cited" if set(cited) <= set(rules.cited_evidence_ids)
                else "retrieved",
                "confidence": LLM_UPGRADE_CONFIDENCE,
                "reason": f"{label} judged it a faithful rewording of the evidence: "
                          f"{review.reason} (Rule check: {rules.reason})",
            })  # fmt: skip
        return rules.model_copy(update={
            "reason": f"{rules.reason} The AI reviewer considered it supported, but the rule "
                      "checks found facts the evidence doesn't state, so it is not approved.",
        })  # fmt: skip
    return rules


def _key(claim: ClaimInput) -> tuple[object, ...]:
    return (claim.text, claim.claim_type, tuple(claim.cited_evidence_ids), claim.record_id,
            repr(sorted((claim.facts or {}).items())))  # fmt: skip


@dataclass
class EngineRun:
    report: VerificationReportOut
    ai_execution_log_id: uuid.UUID | None


async def verify_claims(
    session: AsyncSession,
    user: User,
    knowledge: CandidateKnowledge,
    claims: list[ClaimInput],
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
    *,
    document_type: str = "tailored_resume",
    reuse: Sequence[tuple[ClaimInput, ClaimResult]] = (),
) -> EngineRun:
    """Verify every claim; returns the report (not stored; callers store what they need).

    ``reuse`` passes earlier results for identical claims (same text, citations and
    record) so a second pass over mostly unchanged content doesn't repeat LLM calls.
    """
    known = {_key(c): r for c, r in reuse}
    statements = [c for c in claims if not c.is_record_fact]
    retrieved_lists = await retrieve_verified_for_queries(
        session, knowledge.profile.id, [c.text for c in statements], embedder,
        top_k=RETRIEVE_TOP_K,
    ) if statements else []  # fmt: skip
    retrieved = {
        id(c): [e.evidence_id for e in r] for c, r in zip(statements, retrieved_lists, strict=True)
    }

    assessments = [assess(c, knowledge, retrieved.get(id(c), [])) for c in claims]
    results = [a.result for a in assessments]
    warnings: list[str] = []
    verifier, log_id = "rules", None

    reviewable = [
        n for n, (c, a) in enumerate(zip(claims, assessments, strict=True))
        if not c.is_record_fact and _key(c) not in known and a.offered
    ]  # fmt: skip
    if settings.claim_verifier != "rules" and llm is not None and reviewable:
        reviewer = LLMReviewer(llm)
        verifier = f"rules+{reviewer.name}"
        log = AIExecutionLog(
            user_id=user.id, operation=AIOperation.CLAIM_VERIFICATION, provider=llm.name,
            model=llm.model, prompt_version=PROMPT_VERSION,
        )  # fmt: skip
        items = [
            ReviewItem(
                claim_id=str(n), text=claims[n].text, claim_type=claims[n].claim_type.value,
                evidence=[(str(i), e.content) for i, e in assessments[n].offered.items()],
                rule_status=results[n].verification_status, rule_reason=results[n].reason,
            )
            for n in reviewable
        ]  # fmt: skip
        try:
            reviews, raw = await reviewer.review(items)
        except LLMError as exc:
            log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
            verifier = "rules"
            warnings.append(f"The AI reviewer failed ({exc}); rule checks alone were used.")
        else:
            log.status, log.model = AIExecutionStatus.SUCCESS, raw.model
            log.input_tokens, log.output_tokens = raw.input_tokens, raw.output_tokens
            log.latency_ms = raw.latency_ms
            for n in reviewable:
                results[n] = apply_review(assessments[n], reviews.get(str(n)), knowledge, raw.model)
        session.add(log)
        await session.flush()
        log_id = log.id
    elif settings.claim_verifier == "llm" and llm is None:
        warnings.append(
            "The AI reviewer is not configured (set ANTHROPIC_API_KEY); rule checks alone "
            "were used."
        )

    for n, claim in enumerate(claims):
        prior = known.get(_key(claim))
        if prior is not None:
            results[n] = prior.model_copy(
                update={"section": claim.section, "position": claim.position}
            )
            if prior.method == "llm" and verifier == "rules":
                verifier = "rules+llm"
    report = build_report(
        results, verifier=verifier, document_type=document_type,  # type: ignore[arg-type]
        warnings=warnings,
    )  # fmt: skip
    return EngineRun(report, log_id)
