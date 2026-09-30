"""Cover letter pipeline, connected to the claim verification engine:

evidence retrieval + match report -> letter generation -> claim extraction -> verification
-> regenerate failed sentences -> verification -> remove what still fails -> final
verification (decides the status) -> store.

Failed sentences are never kept or "fixed" silently: each is regenerated from the
candidate's own evidence and verified again, or removed, and every change is audited
with the engine's verdict and reason.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.embeddings import EmbeddingProvider
from app.ai.models import AIExecutionLog, AIExecutionStatus, AIOperation
from app.ai.provider import LLMError, LLMProvider
from app.core.config import Settings
from app.core.errors import ConflictError, FieldErrors, NotFoundError
from app.documents.cover_letter.content import CoverLetterContent, LetterParagraph, LetterSentence
from app.documents.cover_letter.generator import (
    PROMPT_VERSION,
    LetterDraft,
    LLMLetterGenerator,
    RuleLetterGenerator,
    frame,
    greeting_for,
    signature_from,
)
from app.documents.cover_letter.schemas import (
    CoverLetterEdit,
    CoverLetterOut,
    GenerationChanges,
    LetterAuditItem,
)
from app.documents.models import (
    ClaimStatus,
    ClaimVerification,
    CoverLetter,
    DocumentStatus,
    GeneratedClaim,
    VerificationMethod,
    VerificationVerdict,
)
from app.documents.resume.schemas import CitedEvidence
from app.documents.resume.service import _current_report, _owned_job
from app.documents.resume.workspace import EvidenceItem, Workspace, load_workspace
from app.jobs.models import Job
from app.matching.schemas import MatchReportOut
from app.profiles import service as profiles
from app.profiles.models import CandidateEvidence, CandidateProfile
from app.retrieval.service import record_label, with_sources
from app.users.models import User
from app.verification import service as verification
from app.verification.engine import EngineRun, verify_claims
from app.verification.extraction import extract_cover_letter_claims, split_sentences
from app.verification.knowledge import CandidateKnowledge, load_knowledge
from app.verification.models import VerificationTrigger
from app.verification.types import ClaimInput, ClaimResult

REGENERATED = "Regenerated from your evidence."
STATUS_LABELS = {
    VerificationVerdict.SUPPORTED: "Supported",
    VerificationVerdict.PARTIALLY_SUPPORTED: "Partially supported",
    VerificationVerdict.UNSUPPORTED: "Unsupported",
    VerificationVerdict.CONTRADICTED: "Contradicted",
}
Key = tuple[str, int]


@dataclass
class Outcome:
    section: str
    position: int  # where the regenerated sentence ended up, or the original position
    original: str
    final: str | None
    verdict: VerificationVerdict
    reason: str
    confidence: float
    method: str = "rule_based"


def _results(claims: list[ClaimInput], run: EngineRun) -> dict[Key, ClaimResult]:
    return {(c.section, c.position): r for c, r in zip(claims, run.report.claims, strict=True)}


def _pairs(claims: list[ClaimInput], run: EngineRun) -> list[tuple[ClaimInput, ClaimResult]]:
    return list(zip(claims, run.report.claims, strict=True))


def _mapped(
    content: CoverLetterContent, replace: dict[Key, LetterSentence | None]
) -> CoverLetterContent:
    """The letter with sentences replaced (or removed, for None); empty paragraphs dropped."""
    paragraphs = []
    for section, paragraph in ((f"paragraphs:{n}", p) for n, p in enumerate(content.paragraphs)):
        sentences = []
        for position, sentence in enumerate(paragraph.sentences):
            key = (section, position)
            new = replace.get(key, sentence)
            if new is not None:
                sentences.append(new)
        if sentences:
            paragraphs.append(LetterParagraph(sentences=sentences))
    return content.model_copy(update={"paragraphs": paragraphs})


def _cite_supporting_evidence(
    content: CoverLetterContent, results: dict[Key, ClaimResult]
) -> list[str]:
    """Approved sentences cite the evidence that supports them; returns a note for each
    sentence whose citation changed because the engine found different evidence."""
    notes = []
    for section, position, sentence in content.sentences():
        result = results.get((section, position))
        if result is None or not result.approved:
            continue
        if result.evidence_source == "retrieved":
            notes.append(
                f"“{sentence.text[:80]}” didn't cite evidence that supports it; it now "
                "cites your evidence that does."
            )
        sentence.evidence_ids = list(result.evidence_ids)
    return notes


async def _revise(
    session: AsyncSession,
    user: User,
    ws: Workspace,
    llm_generator: LLMLetterGenerator | None,
    failed: list[tuple[ClaimInput, ClaimResult]],
    notes: list[str],
) -> dict[Key, LetterSentence | None]:
    """Regenerate failed sentences from the candidate's evidence (None: nothing usable)."""
    offered: dict[Key, list[EvidenceItem]] = {}
    for claim, result in failed:
        cited = [*claim.cited_evidence_ids, *result.evidence_ids]
        items = [ws.evidence[i] for i in dict.fromkeys(cited) if i in ws.evidence]
        offered[(claim.section, claim.position)] = (
            items or sorted(ws.evidence.values(), key=lambda e: -e.relevance)[:3]
        )
    replacements: dict[Key, LetterSentence | None] = {}
    if llm_generator is not None:
        provider = llm_generator.provider
        log = AIExecutionLog(
            user_id=user.id, operation=AIOperation.COVER_LETTER_GENERATION,
            provider=provider.name, model=provider.model, prompt_version=f"{PROMPT_VERSION}-revise",
        )  # fmt: skip
        keys = {f"{c.section}[{c.position}]": (c.section, c.position) for c, _ in failed}
        try:
            revisions, raw = await llm_generator.revise(ws, [
                (f"{c.section}[{c.position}]", c.text, r.reason, offered[(c.section, c.position)])
                for c, r in failed
            ])  # fmt: skip
        except LLMError as exc:
            log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
            notes.append(f"AI regeneration failed ({exc}); unsupported sentences were removed.")
            revisions = []
        else:
            log.status, log.model = AIExecutionStatus.SUCCESS, raw.model
            log.input_tokens, log.output_tokens = raw.input_tokens, raw.output_tokens
            log.latency_ms = raw.latency_ms
        session.add(log)
        for revision in revisions:
            if revision.sentence_id in keys:
                replacements[keys[revision.sentence_id]] = (
                    LetterSentence(text=revision.text, evidence_ids=revision.evidence_ids)
                    if revision.text else None
                )  # fmt: skip
        return replacements
    for claim, _ in failed:
        key = (claim.section, claim.position)
        own = [ws.evidence[i] for i in claim.cited_evidence_ids if i in ws.evidence]
        text = frame(own[0], ws) if own else None
        replacements[key] = LetterSentence(text=text, evidence_ids=[own[0].id]) if text else None
    return replacements


def _without_repeats(
    content: CoverLetterContent, replacements: dict[Key, LetterSentence | None]
) -> dict[Key, LetterSentence | None]:
    """Drop regenerated sentences that would repeat a sentence already in the letter."""
    seen = {s.text for k, s in ((k, s) for sec, pos, s in content.sentences()
                                for k in [(sec, pos)]) if k not in replacements}  # fmt: skip
    kept: dict[Key, LetterSentence | None] = {}
    for key, sentence in replacements.items():
        if sentence is not None and sentence.text in seen:
            kept[key] = None
        else:
            kept[key] = sentence
            if sentence is not None:
                seen.add(sentence.text)
    return kept


def _label(section: str) -> str:
    if section.startswith("paragraphs:"):
        return f"Paragraph {int(section.split(':')[1]) + 1}"
    return section.capitalize()


async def generate(
    session: AsyncSession,
    user: User,
    job_id: uuid.UUID,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
    match_llm: LLMProvider | None = None,
    verify_llm: LLMProvider | None = None,
) -> CoverLetterOut:
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    match_report = await _current_report(session, user, job, embedder, match_llm, settings)
    ws = await load_workspace(session, profile.id, job, match_report, embedder)

    draft, generator_name, log_id, llm_generator = await _draft(
        session, user, ws, match_report, llm, settings
    )
    knowledge = await load_knowledge(session, profile.id)

    async def run(
        content: CoverLetterContent, reuse: tuple[tuple[ClaimInput, ClaimResult], ...] = ()
    ) -> tuple[list[ClaimInput], EngineRun]:
        claims = extract_cover_letter_claims(content)
        found = await verify_claims(
            session, user, knowledge, claims, embedder, verify_llm, settings,
            document_type="cover_letter", reuse=reuse,
        )  # fmt: skip
        return claims, found

    # 1. Verify the draft.
    content = draft.content
    claims, first = await run(content)
    first_results = _results(claims, first)
    outcomes: list[Outcome] = []
    # A greeting or closing that makes claims is replaced with a plain one.
    for section, plain in (
        ("greeting", greeting_for(content.company_name)),
        ("closing", "Sincerely,"),
    ):
        r = first_results[(section, 0)]
        if not r.approved:
            original = getattr(content, section)
            outcomes.append(Outcome(section, 0, original, plain, r.verification_status,
                                    r.reason, r.confidence, r.method))  # fmt: skip
            content = content.model_copy(update={section: plain})

    # 2. Regenerate the sentences that failed, and verify again. Positions stay stable:
    #    a sentence with no regeneration stays as it was (and fails again).
    failed = [(c, r) for c, r in _pairs(claims, first)
              if c.section.startswith("paragraphs:") and not r.approved]  # fmt: skip
    replacements = _without_repeats(
        content,
        await _revise(session, user, ws, llm_generator, failed, draft.notes) if failed else {},
    )
    revised = _mapped(content, {k: v for k, v in replacements.items() if v is not None})
    claims2, second = await run(revised, reuse=tuple(_pairs(claims, first)))
    second_results = _results(claims2, second)

    # 3. Remove whatever still fails.
    still_failing = {
        k for k, r in second_results.items() if k[0].startswith("paragraphs:") and not r.approved
    }
    for claim, result in failed:
        key = (claim.section, claim.position)
        new = replacements.get(key)
        kept = new is not None and key not in still_failing
        reason = result.reason
        if new is not None and not kept:
            reason += f" The regenerated sentence also failed: {second_results[key].reason}"
        outcomes.append(Outcome(claim.section, claim.position, claim.text,
                                new.text if kept and new else None, result.verification_status,
                                reason, result.confidence, result.method))  # fmt: skip
    final = _mapped(revised, dict.fromkeys(still_failing))

    # 4. Approved sentences cite the evidence that supports them; the final letter is
    #    verified as a whole, and that report decides its status.
    notes = [*draft.notes, *_cite_supporting_evidence(final, _by_text(final, claims2, second))]
    final_claims, final_run = await run(final, reuse=tuple(_pairs(claims2, second)))

    version = (await session.scalar(
        select(func.max(CoverLetter.version)).where(
            CoverLetter.candidate_profile_id == profile.id, CoverLetter.job_id == job.id)
    ) or 0) + 1  # fmt: skip
    await session.execute(
        delete(CoverLetter).where(
            CoverLetter.candidate_profile_id == profile.id, CoverLetter.job_id == job.id,
            CoverLetter.status != DocumentStatus.APPROVED,
        )
    )  # fmt: skip
    letter = CoverLetter(
        candidate_profile_id=profile.id, job_id=job.id, version=version,
        status=_status(final_run), content={}, generator_name=generator_name,
        notes=[*notes, *final_run.report.warnings], ai_execution_log_id=log_id,
    )  # fmt: skip
    session.add(letter)
    await session.flush()
    llm_log = (
        final_run.ai_execution_log_id or second.ai_execution_log_id or first.ai_execution_log_id
    )
    await _store_claims(session, letter, final, final_claims, final_run, outcomes, llm_log)
    letter.content = final.model_dump(mode="json")
    await verification.store_report(
        session, final_run.report, cover_letter_id=letter.id,
        trigger=VerificationTrigger.GENERATION, ai_execution_log_id=llm_log,
    )  # fmt: skip
    await session.commit()
    return await _out(session, letter)


def _by_text(
    content: CoverLetterContent, claims: list[ClaimInput], run: EngineRun
) -> dict[Key, ClaimResult]:
    """Results for the sentences of ``content``, matched by text and keyed by position in
    ``content`` (which may have fewer sentences than the letter that was verified)."""
    by_text = {c.text: r for c, r in _pairs(claims, run) if c.section.startswith("paragraphs:")}
    return {
        (section, position): by_text[s.text]
        for section, position, s in content.sentences()
        if s.text in by_text
    }


async def _draft(
    session: AsyncSession,
    user: User,
    ws: Workspace,
    report: MatchReportOut,
    llm: LLMProvider | None,
    settings: Settings,
) -> tuple[LetterDraft, str, uuid.UUID | None, LLMLetterGenerator | None]:
    rules = RuleLetterGenerator()
    draft = rules.generate(ws, report)
    if settings.cover_letter_generator == "rules" or llm is None:
        if settings.cover_letter_generator == "llm":
            draft.notes.append(
                "AI writing is not configured (set ANTHROPIC_API_KEY); the rule-based letter "
                "was used."
            )
        return draft, rules.name, None, None
    generator = LLMLetterGenerator(llm)
    log = AIExecutionLog(
        user_id=user.id, operation=AIOperation.COVER_LETTER_GENERATION, provider=llm.name,
        model=llm.model, prompt_version=PROMPT_VERSION,
    )  # fmt: skip
    try:
        written, raw = await generator.generate(ws, report)
    except LLMError as exc:
        log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
        draft.notes.append(f"AI writing failed ({exc}); the rule-based letter was used.")
        session.add(log)
        await session.flush()
        return draft, rules.name, log.id, None
    log.status, log.model = AIExecutionStatus.SUCCESS, raw.model
    log.input_tokens, log.output_tokens = raw.input_tokens, raw.output_tokens
    log.latency_ms = raw.latency_ms
    session.add(log)
    await session.flush()
    written.notes = [n for n in draft.notes if n.startswith("Not mentioned")]
    return written, generator.name, log.id, generator


def _status(run: EngineRun) -> DocumentStatus:
    return DocumentStatus.VERIFIED if run.report.outcome == "approved" else (
        DocumentStatus.VERIFICATION_FAILED)  # fmt: skip


def _method(result_method: str) -> VerificationMethod:
    return VerificationMethod.LLM if result_method == "llm" else VerificationMethod.RULE_BASED


async def _store_claims(
    session: AsyncSession,
    letter: CoverLetter,
    content: CoverLetterContent,
    claims: list[ClaimInput],
    run: EngineRun,
    outcomes: list[Outcome],
    llm_log: uuid.UUID | None,
) -> None:
    results = _results(claims, run)
    for section, position, sentence in content.sentences():
        result = results[(section, position)]
        linked = [await session.get(CandidateEvidence, i) for i in result.evidence_ids] if (
            result.approved) else []  # fmt: skip
        record = GeneratedClaim(
            cover_letter_id=letter.id, claim_text=sentence.text, section=section,
            position=position,
            status=ClaimStatus.VERIFIED if result.approved else ClaimStatus.UNSUPPORTED,
            evidence=[e for e in linked if e is not None],
        )  # fmt: skip
        record.verifications.append(ClaimVerification(
            verdict=result.verification_status, method=_method(result.method),
            confidence=result.confidence, rationale=result.reason,
            ai_execution_log_id=llm_log if result.method == "llm" else None,
        ))  # fmt: skip
        session.add(record)
        await session.flush()
        sentence.claim_id = record.id
    for outcome in outcomes:
        removed = GeneratedClaim(
            cover_letter_id=letter.id, claim_text=outcome.original[:5000].strip() or "(blank)",
            section=outcome.section[:50], position=outcome.position, status=ClaimStatus.REMOVED,
        )  # fmt: skip
        removed.verifications.append(ClaimVerification(
            verdict=outcome.verdict, method=_method(outcome.method),
            confidence=outcome.confidence,
            rationale=f"{outcome.reason} {REGENERATED} {outcome.final}" if outcome.final
            else outcome.reason,
            ai_execution_log_id=llm_log if outcome.method == "llm" else None,
        ))  # fmt: skip
        session.add(removed)


# --- Output -----------------------------------------------------------------------------


async def _evidence(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, CitedEvidence]:
    rows = await session.scalars(
        with_sources(select(CandidateEvidence).where(CandidateEvidence.id.in_(ids)))
    )
    return {e.id: CitedEvidence(content=e.content, record_label=record_label(e)) for e in rows}


async def _out(session: AsyncSession, letter: CoverLetter) -> CoverLetterOut:
    await session.refresh(letter)
    claims = list(
        await session.scalars(
            select(GeneratedClaim)
            .where(GeneratedClaim.cover_letter_id == letter.id)
            .options(selectinload(GeneratedClaim.verifications))
        )
    )
    audit = []
    for claim in (c for c in claims if c.status == ClaimStatus.REMOVED):
        check = claim.verifications[-1] if claim.verifications else None
        rationale = check.rationale or "" if check else ""
        reason, _, final = rationale.partition(f" {REGENERATED} ")
        audit.append(LetterAuditItem(
            section=_label(claim.section or ""), original_text=claim.claim_text,
            final_text=final or None, outcome="regenerated" if final else "removed",
            verdict=check.verdict if check else VerificationVerdict.UNSUPPORTED, reason=reason,
        ))  # fmt: skip
    content = CoverLetterContent.model_validate(letter.content)
    reports = await verification.reports_for(session, cover_letter_id=letter.id)
    report = reports[0] if reports else None
    ids = {i for _, _, s in content.sentences() for i in s.evidence_ids}
    if report is not None:
        ids |= {i for r in report.claims for i in r.evidence_ids}
    job = await session.get(Job, letter.job_id)
    assert job is not None  # noqa: S101 - FK guarantees the job exists
    in_document = [c for c in claims if c.status != ClaimStatus.REMOVED]
    return CoverLetterOut(
        id=letter.id, job_id=letter.job_id, job_title=job.title, company_name=job.company_name,
        version=letter.version, status=letter.status, generator=letter.generator_name,
        created_at=letter.created_at, updated_at=letter.updated_at, content=content,
        word_count=content.word_count(),
        changes=GenerationChanges(
            kept=sum(1 for c in in_document if c.status == ClaimStatus.VERIFIED),
            regenerated=sum(1 for a in audit if a.outcome == "regenerated"),
            removed=sum(1 for a in audit if a.outcome == "removed"),
            audit=audit,
        ),
        notes=letter.notes, evidence=await _evidence(session, ids), report=report,
    )  # fmt: skip


async def _owned_letter(session: AsyncSession, user: User, letter_id: uuid.UUID) -> CoverLetter:
    profile = await profiles.get_profile(session, user)
    letter = await session.scalar(
        select(CoverLetter)
        .where(CoverLetter.id == letter_id, CoverLetter.candidate_profile_id == profile.id)
        .execution_options(populate_existing=True)
    )
    if letter is None:
        raise NotFoundError("Cover letter not found.")
    return letter


async def latest(session: AsyncSession, user: User, job_id: uuid.UUID) -> CoverLetterOut:
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    letter = await session.scalar(
        select(CoverLetter)
        .where(CoverLetter.candidate_profile_id == profile.id, CoverLetter.job_id == job.id)
        .order_by(CoverLetter.version.desc())
        .limit(1)
    )
    if letter is None:
        raise NotFoundError("No cover letter for this job yet.")
    return await _out(session, letter)


async def get(session: AsyncSession, user: User, letter_id: uuid.UUID) -> CoverLetter:
    return await _owned_letter(session, user, letter_id)


async def output(session: AsyncSession, letter: CoverLetter) -> CoverLetterOut:
    return await _out(session, letter)


def as_content(letter: CoverLetter) -> CoverLetterContent:
    return CoverLetterContent.model_validate(letter.content)


# --- Editing and re-verification --------------------------------------------------------


def _error_key(result: ClaimResult) -> str:
    if result.section.startswith("paragraphs:"):
        return f"paragraphs[{result.section.split(':')[1]}]"
    return result.section


def edited_content(
    existing: CoverLetterContent, edit: CoverLetterEdit, profile: CandidateProfile
) -> CoverLetterContent:
    """The edited letter. Unchanged sentences keep their citations; changed or new ones
    start with none (the engine then looks for evidence that supports them)."""
    previous = {s.text: s.evidence_ids for _, _, s in existing.sentences()}
    paragraphs = [
        LetterParagraph(sentences=[
            LetterSentence(text=text, evidence_ids=list(previous.get(text, [])))
            for text in split_sentences(paragraph)
        ])
        for paragraph in edit.paragraphs if paragraph.strip()
    ]  # fmt: skip
    return existing.model_copy(update={
        "greeting": " ".join(edit.greeting.split()),
        "closing": " ".join(edit.closing.split()),
        "paragraphs": [p for p in paragraphs if p.sentences],
        # The signature always shows the profile as it is now.
        "signature": signature_from(profile),
    })  # fmt: skip


async def update(
    session: AsyncSession,
    user: User,
    letter_id: uuid.UUID,
    edit: CoverLetterEdit,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> CoverLetterOut:
    """Save edits, only if the engine approves every sentence; otherwise 422 with reasons."""
    letter = await _owned_letter(session, user, letter_id)
    if letter.status == DocumentStatus.APPROVED:
        raise ConflictError("Approved cover letters can't be edited; regenerate to make changes.")
    knowledge: CandidateKnowledge = await load_knowledge(session, letter.candidate_profile_id)
    content = edited_content(as_content(letter), edit, knowledge.profile)
    if not content.paragraphs:
        raise FieldErrors({"paragraphs": "The letter needs at least one paragraph."})
    claims = extract_cover_letter_claims(content)
    run = await verify_claims(session, user, knowledge, claims, embedder, llm, settings,
                              document_type="cover_letter")  # fmt: skip
    errors: dict[str, str] = {}
    for result in run.report.claims:
        if not result.approved:
            key = _error_key(result)
            message = (f"{STATUS_LABELS[result.verification_status]}: "
                       f"“{result.claim_text[:80]}”. {result.reason}")  # fmt: skip
            errors[key] = f"{errors[key]} {message}" if key in errors else message
    if errors:
        await session.commit()  # keeps the AI execution log, if any
        raise FieldErrors(errors)

    notes = _cite_supporting_evidence(content, _results(claims, run))
    await session.execute(delete(GeneratedClaim).where(GeneratedClaim.cover_letter_id == letter.id))
    await _store_claims(session, letter, content, claims, run, [], run.ai_execution_log_id)
    letter.content = content.model_dump(mode="json")
    letter.status = DocumentStatus.VERIFIED
    letter.notes = [*[n for n in letter.notes if not n.endswith("cites your evidence that does.")],
                    *notes]  # fmt: skip
    await verification.store_report(
        session, run.report, cover_letter_id=letter.id, trigger=VerificationTrigger.EDIT,
        ai_execution_log_id=run.ai_execution_log_id,
    )  # fmt: skip
    await session.commit()
    return await _out(session, await _owned_letter(session, user, letter_id))


async def reverify(
    session: AsyncSession,
    user: User,
    letter_id: uuid.UUID,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> CoverLetterOut:
    """Verify a stored letter again; failures are reported and marked, never fixed."""
    letter = await _owned_letter(session, user, letter_id)
    content = as_content(letter)
    knowledge = await load_knowledge(session, letter.candidate_profile_id)
    claims = extract_cover_letter_claims(content)
    run = await verify_claims(session, user, knowledge, claims, embedder, llm, settings,
                              document_type="cover_letter")  # fmt: skip
    results = _results(claims, run)
    stored = await session.scalars(
        select(GeneratedClaim).where(
            GeneratedClaim.cover_letter_id == letter.id,
            GeneratedClaim.status != ClaimStatus.REMOVED,
        )
    )
    for claim in stored:
        result = results.get((claim.section or "", claim.position))
        if result is None or result.claim_text != claim.claim_text:
            continue
        claim.status = ClaimStatus.VERIFIED if result.approved else ClaimStatus.UNSUPPORTED
        session.add(ClaimVerification(
            generated_claim_id=claim.id, verdict=result.verification_status,
            method=_method(result.method), confidence=result.confidence,
            rationale=result.reason,
            ai_execution_log_id=run.ai_execution_log_id if result.method == "llm" else None,
        ))  # fmt: skip
    if letter.status != DocumentStatus.APPROVED:
        letter.status = _status(run)
    await verification.store_report(
        session, run.report, cover_letter_id=letter.id, trigger=VerificationTrigger.MANUAL,
        ai_execution_log_id=run.ai_execution_log_id,
    )  # fmt: skip
    await session.commit()
    return await _out(session, await _owned_letter(session, user, letter_id))


async def delete_letter(session: AsyncSession, user: User, letter_id: uuid.UUID) -> None:
    letter = await _owned_letter(session, user, letter_id)
    if letter.status == DocumentStatus.APPROVED:
        raise ConflictError("Approved cover letters can't be deleted.")
    await session.delete(letter)
    await session.commit()
