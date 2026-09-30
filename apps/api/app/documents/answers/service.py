"""Application answers, connected to the claim verification engine.

For each question: understand it -> retrieve relevant verified evidence -> generate an
answer -> verify every sentence -> regenerate failed sentences from evidence or remove
them -> verify the final answer (which decides its status) -> store, with the evidence
used. Approval re-verifies against the current profile first; editing withdraws it.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.embeddings import EmbeddingProvider
from app.ai.models import AIExecutionLog, AIExecutionStatus, AIOperation
from app.ai.provider import LLMError, LLMProvider
from app.core.config import Settings
from app.core.errors import ConflictError, FieldErrors, NotFoundError
from app.documents.answers.generator import (
    PROMPT_VERSION,
    AnswerDraft,
    LLMAnswerGenerator,
    RuleAnswerGenerator,
)
from app.documents.answers.questions import Understanding, summary_of, understand
from app.documents.answers.schemas import (
    AnswerEdit,
    ApplicationAnswerOut,
    EvidenceUsed,
    QuestionsIn,
)
from app.documents.cover_letter.content import LetterSentence
from app.documents.cover_letter.generator import frame
from app.documents.cover_letter.schemas import GenerationChanges, LetterAuditItem
from app.documents.models import (
    ApplicationAnswer,
    ClaimStatus,
    ClaimVerification,
    DocumentStatus,
    GeneratedClaim,
    VerificationMethod,
    VerificationVerdict,
)
from app.documents.resume.service import _current_report, _owned_job
from app.documents.resume.workspace import EvidenceItem, Workspace, load_workspace
from app.jobs.models import Job
from app.matching.schemas import MatchReportOut
from app.profiles import service as profiles
from app.profiles.models import CandidateEvidence
from app.retrieval.service import record_label, retrieve_verified_for_queries, with_sources
from app.users.models import User
from app.verification import service as verification
from app.verification.engine import EngineRun, verify_claims
from app.verification.extraction import extract_answer_claims, split_sentences
from app.verification.knowledge import CandidateKnowledge, load_knowledge
from app.verification.models import VerificationTrigger
from app.verification.types import ClaimInput, ClaimResult

REGENERATED = "Regenerated from your evidence."
RETRIEVE_TOP_K = 8
STATUS_LABELS = {
    VerificationVerdict.SUPPORTED: "Supported",
    VerificationVerdict.PARTIALLY_SUPPORTED: "Partially supported",
    VerificationVerdict.UNSUPPORTED: "Unsupported",
    VerificationVerdict.CONTRADICTED: "Contradicted",
}


@dataclass
class Outcome:
    position: int
    original: str
    final: str | None
    verdict: VerificationVerdict
    reason: str
    confidence: float
    method: str


@dataclass
class Context:
    """Everything one generation needs, loaded once per request."""

    session: AsyncSession
    user: User
    ws: Workspace
    match_report: MatchReportOut
    knowledge: CandidateKnowledge
    embedder: EmbeddingProvider
    llm: LLMProvider | None
    verify_llm: LLMProvider | None
    settings: Settings

    @property
    def allowed_names(self) -> list[str]:
        return [self.ws.job.title, self.ws.job.company_name]


def _sentences(answer: ApplicationAnswer) -> list[LetterSentence]:
    return [LetterSentence.model_validate(s) for s in (answer.answer or {}).get("sentences", [])]


def _method(name: str) -> VerificationMethod:
    return VerificationMethod.LLM if name == "llm" else VerificationMethod.RULE_BASED


async def _verify(
    ctx: Context,
    sentences: list[LetterSentence],
    reuse: tuple[tuple[ClaimInput, ClaimResult], ...] = (),
) -> tuple[list[ClaimInput], EngineRun]:
    claims = extract_answer_claims([(s.text, s.evidence_ids) for s in sentences], ctx.allowed_names)
    run = await verify_claims(
        ctx.session,
        ctx.user,
        ctx.knowledge,
        claims,
        ctx.embedder,
        ctx.verify_llm,
        ctx.settings,
        document_type="application_answer",
        reuse=reuse,
    )
    return claims, run


async def _retrieve(ctx: Context, understanding: Understanding) -> list[EvidenceItem]:
    """Verified evidence for the question, most similar first (then by job relevance)."""
    [found] = await retrieve_verified_for_queries(
        ctx.session,
        ctx.knowledge.profile.id,
        [understanding.query],
        ctx.embedder,
        top_k=RETRIEVE_TOP_K,
    )
    return [ctx.ws.evidence[e.evidence_id] for e in found if e.evidence_id in ctx.ws.evidence]


async def _draft(
    ctx: Context,
    answer: ApplicationAnswer,
    understanding: Understanding,
    retrieved: list[EvidenceItem],
) -> tuple[AnswerDraft, str, uuid.UUID | None, LLMAnswerGenerator | None]:
    rules = RuleAnswerGenerator()
    draft = rules.generate(ctx.ws, ctx.match_report, understanding, retrieved, answer.max_words)
    if ctx.settings.answer_generator == "rules" or ctx.llm is None:
        if ctx.settings.answer_generator == "llm":
            draft.notes.append(
                "AI writing is not configured (set ANTHROPIC_API_KEY); a rule-based answer was "
                "used."
            )
        return draft, rules.name, None, None
    generator = LLMAnswerGenerator(ctx.llm)
    log = AIExecutionLog(
        user_id=ctx.user.id,
        operation=AIOperation.APPLICATION_ANSWER,
        provider=ctx.llm.name,
        model=ctx.llm.model,
        prompt_version=PROMPT_VERSION,
    )
    try:
        written, raw = await generator.generate(
            ctx.ws, answer.question, understanding, retrieved, answer.max_words
        )
    except LLMError as exc:
        log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
        draft.notes.append(f"AI writing failed ({exc}); a rule-based answer was used.")
        ctx.session.add(log)
        await ctx.session.flush()
        return draft, rules.name, log.id, None
    log.status, log.model = AIExecutionStatus.SUCCESS, raw.model
    log.input_tokens, log.output_tokens = raw.input_tokens, raw.output_tokens
    log.latency_ms = raw.latency_ms
    ctx.session.add(log)
    await ctx.session.flush()
    if not written.sentences:
        written.notes.append("Your verified evidence doesn't cover this question.")
    return written, generator.name, log.id, generator


async def _regenerate(
    ctx: Context,
    generator: LLMAnswerGenerator | None,
    failed: list[tuple[int, ClaimInput, ClaimResult]],
    notes: list[str],
) -> dict[int, LetterSentence | None]:
    """Regenerate failed sentences from the candidate's evidence (None: nothing usable)."""
    ws = ctx.ws
    offered: dict[int, list[EvidenceItem]] = {}
    for n, claim, result in failed:
        ids = [*claim.cited_evidence_ids, *result.evidence_ids]
        items = [ws.evidence[i] for i in dict.fromkeys(ids) if i in ws.evidence]
        offered[n] = items or sorted(ws.evidence.values(), key=lambda e: -e.relevance)[:3]
    if generator is None:
        replacements: dict[int, LetterSentence | None] = {}
        for n, claim, _ in failed:
            own = [ws.evidence[i] for i in claim.cited_evidence_ids if i in ws.evidence]
            text = frame(own[0], ws) if own else None
            replacements[n] = LetterSentence(text=text, evidence_ids=[own[0].id]) if text else None
        return replacements
    provider = generator.provider
    log = AIExecutionLog(
        user_id=ctx.user.id,
        operation=AIOperation.APPLICATION_ANSWER,
        provider=provider.name,
        model=provider.model,
        prompt_version=f"{PROMPT_VERSION}-revise",
    )
    replacements = {}
    try:
        revisions, raw = await generator.revise(
            [(str(n), c.text, r.reason, offered[n]) for n, c, r in failed]
        )
    except LLMError as exc:
        log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
        notes.append(f"AI regeneration failed ({exc}); unsupported sentences were removed.")
        revisions = []
    else:
        log.status, log.model = AIExecutionStatus.SUCCESS, raw.model
        log.input_tokens, log.output_tokens = raw.input_tokens, raw.output_tokens
        log.latency_ms = raw.latency_ms
    ctx.session.add(log)
    for revision in revisions:
        replacements[int(revision.sentence_id)] = (
            LetterSentence(text=revision.text, evidence_ids=revision.evidence_ids)
            if revision.text
            else None
        )
    return replacements


def _cite(sentences: list[LetterSentence], results: list[ClaimResult]) -> list[str]:
    """Approved sentences cite the evidence that supports them (noting any change)."""
    notes = []
    for sentence, result in zip(sentences, results, strict=True):
        if not result.approved:
            continue
        if result.evidence_source == "retrieved":
            notes.append(
                f"“{sentence.text[:80]}” didn't cite evidence that supports it; it now "
                "cites your evidence that does."
            )
        sentence.evidence_ids = list(result.evidence_ids)
    return notes


def _status(sentences: list[LetterSentence], run: EngineRun) -> DocumentStatus:
    if not sentences:
        return DocumentStatus.DRAFT  # nothing to answer with yet
    return (
        DocumentStatus.VERIFIED
        if run.report.outcome == "approved"
        else (DocumentStatus.VERIFICATION_FAILED)
    )


async def _store(
    ctx: Context,
    answer: ApplicationAnswer,
    sentences: list[LetterSentence],
    run: EngineRun,
    outcomes: list[Outcome],
    llm_log: uuid.UUID | None,
    trigger: VerificationTrigger,
) -> None:
    session = ctx.session
    await session.execute(
        delete(GeneratedClaim).where(GeneratedClaim.application_answer_id == answer.id)
    )
    for position, (sentence, result) in enumerate(zip(sentences, run.report.claims, strict=True)):
        linked = (
            [await session.get(CandidateEvidence, i) for i in result.evidence_ids]
            if (result.approved)
            else []
        )
        claim = GeneratedClaim(
            application_answer_id=answer.id,
            claim_text=sentence.text,
            section="answer",
            position=position,
            status=ClaimStatus.VERIFIED if result.approved else ClaimStatus.UNSUPPORTED,
            evidence=[e for e in linked if e is not None],
        )
        claim.verifications.append(
            ClaimVerification(
                verdict=result.verification_status,
                method=_method(result.method),
                confidence=result.confidence,
                rationale=result.reason,
                ai_execution_log_id=llm_log if result.method == "llm" else None,
            )
        )
        session.add(claim)
        await session.flush()
        sentence.claim_id = claim.id
    for outcome in outcomes:
        removed = GeneratedClaim(
            application_answer_id=answer.id,
            claim_text=outcome.original[:5000].strip() or "(blank)",
            section="answer",
            position=outcome.position,
            status=ClaimStatus.REMOVED,
        )
        removed.verifications.append(
            ClaimVerification(
                verdict=outcome.verdict,
                method=_method(outcome.method),
                confidence=outcome.confidence,
                rationale=f"{outcome.reason} {REGENERATED} {outcome.final}"
                if outcome.final
                else outcome.reason,
                ai_execution_log_id=llm_log if outcome.method == "llm" else None,
            )
        )
        session.add(removed)
    answer.answer = {"sentences": [s.model_dump(mode="json") for s in sentences]}
    await verification.store_report(
        session,
        run.report,
        application_answer_id=answer.id,
        trigger=trigger,
        ai_execution_log_id=llm_log,
    )


async def _generate_into(ctx: Context, answer: ApplicationAnswer) -> None:
    """Understand, retrieve, generate, verify, regenerate or remove, verify, store."""
    understanding = understand(answer.question, ctx.ws.job.title)
    answer.question_type, answer.focus = understanding.question_type, understanding.focus
    retrieved = await _retrieve(ctx, understanding)
    draft, generator_name, log_id, generator = await _draft(ctx, answer, understanding, retrieved)

    sentences = draft.sentences
    claims, first = await _verify(ctx, sentences)
    failed = [
        (n, c, r)
        for n, (c, r) in enumerate(zip(claims, first.report.claims, strict=True))
        if not r.approved
    ]
    replacements = await _regenerate(ctx, generator, failed, draft.notes) if failed else {}
    # A regeneration that repeats another sentence is dropped.
    keep = {s.text for n, s in enumerate(sentences) if n not in replacements}
    for n, new in list(replacements.items()):
        if new is not None and new.text in keep:
            replacements[n] = None
        elif new is not None:
            keep.add(new.text)
    revised = [replacements.get(n) or s for n, s in enumerate(sentences)]
    claims2, second = await _verify(
        ctx, revised, tuple(zip(claims, first.report.claims, strict=True))
    )

    outcomes: list[Outcome] = []
    for n, claim, result in failed:
        new = replacements.get(n)
        kept = new is not None and second.report.claims[n].approved
        reason = result.reason
        if new is not None and not kept:
            reason += f" The regenerated sentence also failed: {second.report.claims[n].reason}"
        outcomes.append(
            Outcome(
                n,
                claim.text,
                new.text if kept and new else None,
                result.verification_status,
                reason,
                result.confidence,
                result.method,
            )
        )
    survivors = [(s, r) for s, r in zip(revised, second.report.claims, strict=True) if r.approved]
    final = [s.model_copy() for s, _ in survivors]
    notes = [*draft.notes, *_cite(final, [r for _, r in survivors])]
    _, final_run = await _verify(ctx, final, tuple(zip(claims2, second.report.claims, strict=True)))

    llm_log = (
        final_run.ai_execution_log_id or second.ai_execution_log_id or first.ai_execution_log_id
    )
    answer.generator_name, answer.ai_execution_log_id = generator_name, log_id
    answer.notes = [*notes, *final_run.report.warnings]
    answer.status, answer.approved_at = _status(final, final_run), None
    await _store(ctx, answer, final, final_run, outcomes, llm_log, VerificationTrigger.GENERATION)


async def _context(
    session: AsyncSession,
    user: User,
    job: Job,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
    match_llm: LLMProvider | None,
    verify_llm: LLMProvider | None,
) -> Context:
    profile = await profiles.get_profile(session, user)
    match_report = await _current_report(session, user, job, embedder, match_llm, settings)
    ws = await load_workspace(session, profile.id, job, match_report, embedder)
    knowledge = await load_knowledge(session, profile.id)
    return Context(session, user, ws, match_report, knowledge, embedder, llm, verify_llm, settings)


# --- Public operations ------------------------------------------------------------------


async def create(
    session: AsyncSession,
    user: User,
    job_id: uuid.UUID,
    payload: QuestionsIn,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
    match_llm: LLMProvider | None = None,
    verify_llm: LLMProvider | None = None,
) -> list[ApplicationAnswerOut]:
    """Answer each question from the candidate's verified evidence."""
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    ctx = await _context(session, user, job, embedder, llm, settings, match_llm, verify_llm)
    start = (
        await session.scalar(
            select(func.max(ApplicationAnswer.position)).where(
                ApplicationAnswer.candidate_profile_id == profile.id,
                ApplicationAnswer.job_id == job.id,
            )
        )
        or 0
    ) + 1
    answers = []
    for offset, question in enumerate(payload.questions):
        answer = ApplicationAnswer(
            candidate_profile_id=profile.id,
            job_id=job.id,
            question=question,
            question_type=understand(question, job.title).question_type,
            position=start + offset,
            max_words=payload.max_words,
            answer={"sentences": []},
        )
        session.add(answer)
        await session.flush()
        await _generate_into(ctx, answer)
        answers.append(answer)
    await session.commit()
    return [await _out(session, a) for a in answers]


async def _owned_answer(
    session: AsyncSession, user: User, answer_id: uuid.UUID
) -> ApplicationAnswer:
    profile = await profiles.get_profile(session, user)
    answer = await session.scalar(
        select(ApplicationAnswer)
        .where(
            ApplicationAnswer.id == answer_id, ApplicationAnswer.candidate_profile_id == profile.id
        )
        .execution_options(populate_existing=True)
    )
    if answer is None:
        raise NotFoundError("Answer not found.")
    return answer


async def list_for_job(
    session: AsyncSession, user: User, job_id: uuid.UUID
) -> list[ApplicationAnswerOut]:
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    answers = await session.scalars(
        select(ApplicationAnswer)
        .where(
            ApplicationAnswer.candidate_profile_id == profile.id, ApplicationAnswer.job_id == job.id
        )
        .order_by(ApplicationAnswer.position)
    )
    return [await _out(session, a) for a in answers]


async def get(session: AsyncSession, user: User, answer_id: uuid.UUID) -> ApplicationAnswerOut:
    return await _out(session, await _owned_answer(session, user, answer_id))


async def regenerate(
    session: AsyncSession,
    user: User,
    answer_id: uuid.UUID,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
    match_llm: LLMProvider | None = None,
    verify_llm: LLMProvider | None = None,
) -> ApplicationAnswerOut:
    """Write the answer again from current evidence (withdraws any approval)."""
    answer = await _owned_answer(session, user, answer_id)
    job = await _owned_job(session, user, answer.job_id)
    ctx = await _context(session, user, job, embedder, llm, settings, match_llm, verify_llm)
    await _generate_into(ctx, answer)
    await session.commit()
    return await _out(session, answer)


async def _edit_context(
    session: AsyncSession,
    user: User,
    answer: ApplicationAnswer,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> Context:
    """A lighter context for checking existing text (no match report or generator)."""
    job = await _owned_job(session, user, answer.job_id)
    knowledge = await load_knowledge(session, answer.candidate_profile_id)
    ws = Workspace(profile=knowledge.profile, job=job)
    return Context(session, user, ws, None, knowledge, embedder, None, llm, settings)  # type: ignore[arg-type]


async def update(
    session: AsyncSession,
    user: User,
    answer_id: uuid.UUID,
    edit: AnswerEdit,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> ApplicationAnswerOut:
    """Save an edited answer, only if every sentence is supported. Editing an approved
    answer withdraws the approval."""
    answer = await _owned_answer(session, user, answer_id)
    ctx = await _edit_context(session, user, answer, embedder, llm, settings)
    previous = {s.text: s.evidence_ids for s in _sentences(answer)}
    sentences = [
        LetterSentence(text=t, evidence_ids=list(previous.get(t, [])))
        for t in split_sentences(edit.answer)
    ]
    if not sentences:
        raise FieldErrors({"answer": "The answer needs at least one sentence."})
    _, run = await _verify(ctx, sentences)
    problems = [
        f"{STATUS_LABELS[r.verification_status]}: “{r.claim_text[:80]}”. {r.reason}"
        for r in run.report.claims
        if not r.approved
    ]
    if problems:
        await session.commit()  # keeps the AI execution log, if any
        raise FieldErrors({"answer": " ".join(problems)})
    notes = _cite(sentences, run.report.claims)
    was_approved = answer.status == DocumentStatus.APPROVED
    answer.status, answer.approved_at = DocumentStatus.VERIFIED, None
    answer.notes = [n for n in answer.notes if not n.endswith("cites your evidence that does.")]
    answer.notes += notes
    if was_approved:
        answer.notes.append("Editing withdrew your approval; approve the answer again.")
    await _store(ctx, answer, sentences, run, [], run.ai_execution_log_id, VerificationTrigger.EDIT)
    await session.commit()
    return await _out(session, await _owned_answer(session, user, answer_id))


async def approve(
    session: AsyncSession,
    user: User,
    answer_id: uuid.UUID,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> ApplicationAnswerOut:
    """Approve the answer, after verifying it once more against the current profile."""
    answer = await _owned_answer(session, user, answer_id)
    sentences = _sentences(answer)
    if not sentences:
        raise ConflictError("There is no answer to approve yet. Write one or regenerate it.")
    ctx = await _edit_context(session, user, answer, embedder, llm, settings)
    _, run = await _verify(ctx, sentences)
    if run.report.outcome != "approved":
        answer.status, answer.approved_at = DocumentStatus.VERIFICATION_FAILED, None
        await _store(
            ctx, answer, sentences, run, [], run.ai_execution_log_id, VerificationTrigger.APPROVAL
        )
        await session.commit()
        raise ConflictError(
            "This answer no longer passes verification against your current evidence and "
            "profile, so it can't be approved. Review the report, then edit or regenerate it."
        )
    answer.status, answer.approved_at = DocumentStatus.APPROVED, datetime.now(UTC)
    await _store(
        ctx, answer, sentences, run, [], run.ai_execution_log_id, VerificationTrigger.APPROVAL
    )
    await session.commit()
    return await _out(session, await _owned_answer(session, user, answer_id))


async def delete_answer(session: AsyncSession, user: User, answer_id: uuid.UUID) -> None:
    answer = await _owned_answer(session, user, answer_id)
    await session.delete(answer)  # its claims, evidence links and reports cascade
    await session.commit()


# --- Output -----------------------------------------------------------------------------


async def _out(session: AsyncSession, answer: ApplicationAnswer) -> ApplicationAnswerOut:
    await session.refresh(answer)
    sentences = _sentences(answer)
    claims = list(
        await session.scalars(
            select(GeneratedClaim)
            .where(GeneratedClaim.application_answer_id == answer.id)
            .options(selectinload(GeneratedClaim.verifications))
        )
    )
    audit = []
    for claim in (c for c in claims if c.status == ClaimStatus.REMOVED):
        check = claim.verifications[-1] if claim.verifications else None
        rationale = (check.rationale or "") if check else ""
        reason, _, final = rationale.partition(f" {REGENERATED} ")
        audit.append(
            LetterAuditItem(
                section=f"Sentence {claim.position + 1}",
                original_text=claim.claim_text,
                final_text=final or None,
                outcome="regenerated" if final else "removed",
                verdict=check.verdict if check else VerificationVerdict.UNSUPPORTED,
                reason=reason,
            )
        )
    ids = list(dict.fromkeys(i for s in sentences for i in s.evidence_ids))
    rows = {
        e.id: e
        for e in await session.scalars(
            with_sources(select(CandidateEvidence).where(CandidateEvidence.id.in_(ids)))
        )
    }
    used = [
        EvidenceUsed(evidence_id=i, content=rows[i].content, record_label=record_label(rows[i]))
        for i in ids
        if i in rows
    ]
    reports = await verification.reports_for(session, application_answer_id=answer.id)
    text = " ".join(s.text for s in sentences)
    return ApplicationAnswerOut(
        id=answer.id,
        job_id=answer.job_id,
        question=answer.question,
        question_type=answer.question_type,
        focus=answer.focus,
        understanding=summary_of(answer.question_type, answer.focus),
        position=answer.position,
        max_words=answer.max_words,
        status=answer.status,
        approved_at=answer.approved_at,
        generator=answer.generator_name,
        created_at=answer.created_at,
        updated_at=answer.updated_at,
        sentences=sentences,
        text=text,
        word_count=len(text.split()),
        evidence_used=used,
        changes=GenerationChanges(
            kept=sum(1 for c in claims if c.status == ClaimStatus.VERIFIED),
            regenerated=sum(1 for a in audit if a.outcome == "regenerated"),
            removed=sum(1 for a in audit if a.outcome == "removed"),
            audit=audit,
        ),
        notes=answer.notes,
        report=reports[0] if reports else None,
    )
