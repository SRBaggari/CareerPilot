"""Tailored resume pipeline (claim-first), connected to the claim verification engine:

evidence retrieval -> candidate claims -> resume generation -> claim extraction ->
claim verification (app.verification) -> final resume.

The engine judges every claim. Claims it doesn't approve are rewritten to the evidence
they cite (verbatim) or removed, and the final resume is verified again, independently,
before it is stored: a resume is VERIFIED only if the engine approves every claim in it,
and VERIFICATION_FAILED otherwise. Tailored resumes live in ``tailored_resumes``,
separate from the uploaded master resumes.
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
from app.documents.models import (
    ClaimStatus,
    ClaimVerification,
    DocumentStatus,
    GeneratedClaim,
    TailoredResume,
    VerificationMethod,
    VerificationVerdict,
)
from app.documents.resume.content import Claim, ResumeContent
from app.documents.resume.generator import PROMPT_VERSION, LLMGenerator, RuleGenerator
from app.documents.resume.schemas import (
    AuditItem,
    CitedEvidence,
    TailoredResumeOut,
    VerificationSummary,
)
from app.documents.resume.workspace import load_workspace
from app.jobs.models import Job
from app.matching import service as matching
from app.matching.schemas import MatchReportOut
from app.profiles import service as profiles
from app.profiles.models import CandidateEvidence, Resume
from app.retrieval.service import record_label, with_sources
from app.users.models import User
from app.verification import service as verification
from app.verification.engine import EngineRun, verify_claims
from app.verification.extraction import extract_resume_claims
from app.verification.knowledge import CandidateKnowledge, load_knowledge
from app.verification.models import VerificationTrigger
from app.verification.types import RECORD_BOUND_TYPES, ClaimInput, ClaimResult, ClaimType

REWRITTEN = "Rewritten to the cited evidence."
STATUS_LABELS = {
    VerificationVerdict.SUPPORTED: "Supported",
    VerificationVerdict.PARTIALLY_SUPPORTED: "Partially supported",
    VerificationVerdict.UNSUPPORTED: "Unsupported",
    VerificationVerdict.CONTRADICTED: "Contradicted",
}


@dataclass
class Outcome:
    """A generated claim that didn't survive verification as written."""

    section: str
    position: int  # where its rewrite ended up, or -1 if it was removed
    original: str
    final: Claim | None
    verdict: VerificationVerdict
    reason: str
    confidence: float
    method: str = "rule_based"


# --- Applying verdicts ------------------------------------------------------------------


def apply_verdicts(
    content: ResumeContent,
    claims: list[ClaimInput],
    results: list[ClaimResult],
    knowledge: CandidateKnowledge,
) -> tuple[ResumeContent, list[Outcome], list[str]]:
    """Keep approved claims (citing the evidence that supports them); rewrite unapproved
    bullets to their own evidence, verbatim; remove everything else unapproved.

    Returns the content, the claims that changed, and a note for each kept claim that now
    cites different evidence than it was generated with.
    """
    content = content.model_copy(deep=True)
    verdicts = {
        (c.section, c.position, c.claim_type): (c, r) for c, r in zip(claims, results, strict=True)
    }
    outcomes: list[Outcome] = []
    notes: list[str] = []

    def outcome(result: ClaimResult, final: Claim | None, at: int) -> Outcome:
        return Outcome(result.section, at, result.claim_text, final, result.verification_status,
                       result.reason, result.confidence, result.method)  # fmt: skip

    def keep(section: str, items: list[Claim], claim_type: ClaimType) -> list[Claim]:
        kept: list[Claim] = []
        pending: list[tuple[ClaimResult, Claim | None]] = []
        for position, item in enumerate(items):
            claim, result = verdicts[(section, position, claim_type)]
            final: Claim | None = None
            if result.approved:
                final = Claim(text=item.text, evidence_ids=result.evidence_ids)
                if result.evidence_source == "retrieved":
                    notes.append(
                        f"“{item.text[:80]}” didn't cite evidence that supports it; "
                        "it now cites your evidence that does."
                    )
            elif claim_type in RECORD_BOUND_TYPES:
                scope = knowledge.scope(claim)
                own = [i for i in claim.cited_evidence_ids if i in scope]
                if own:  # the evidence it cited (the item's own, verified), word for word
                    final = Claim(text=scope[own[0]].content, evidence_ids=[own[0]])
            if final is not None and all(k.text != final.text for k in kept):
                kept.append(final)
            if not result.approved:
                pending.append((result, final))
        for result, final in pending:
            at = next((i for i, k in enumerate(kept) if final and k.text == final.text), -1)
            outcomes.append(outcome(result, final if at >= 0 else None, at))
        return kept

    content.summary = keep("summary", content.summary, ClaimType.SUMMARY)
    content.skills = keep("skills", content.skills, ClaimType.SKILL)
    for job in content.experience:
        job.bullets = keep(f"experience:{job.record_id}", job.bullets, ClaimType.EXPERIENCE)
    for project in content.projects:
        section = f"projects:{project.record_id}"
        project.bullets = keep(section, project.bullets, ClaimType.PROJECT)

    # Record facts are copied from the profile, so these only fail if the profile changed
    # mid-generation. Unapproved ones are removed, never "corrected".
    failed = {
        (c.section, c.position)
        for c, r in zip(claims, results, strict=True)
        if c.is_record_fact and not r.approved and c.claim_type != ClaimType.CONTACT
    }
    for section in ("experience", "projects", "education", "certifications", "achievements",
                    "coursework"):  # fmt: skip
        entries = getattr(content, section)
        setattr(content, section, [e for i, e in enumerate(entries) if (section, i) not in failed])
    for c, r in zip(claims, results, strict=True):
        if (c.section, c.position) in failed:
            outcomes.append(outcome(r, None, -1))
    return content, outcomes, notes


# --- Loading and output -----------------------------------------------------------------


async def _owned_job(session: AsyncSession, user: User, job_id: uuid.UUID) -> Job:
    job = await session.scalar(
        select(Job)
        .where(Job.id == job_id, Job.created_by_user_id == user.id)
        .options(selectinload(Job.requirements))
    )
    if job is None:
        raise NotFoundError("Job not found.")
    return job


async def _current_report(
    session: AsyncSession, user: User, job: Job, embedder: EmbeddingProvider,
    llm: LLMProvider | None, settings: Settings,
) -> MatchReportOut:  # fmt: skip
    """The job match report, computed or refreshed when missing or stale."""
    try:
        report = await matching.get_report(session, user, job.id)
    except NotFoundError:
        report = None
    if report is None or report.is_stale:
        report = await matching.compute_match(session, user, job.id, embedder, llm, settings)
    return report


async def _evidence(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, CitedEvidence]:
    rows = await session.scalars(
        with_sources(select(CandidateEvidence).where(CandidateEvidence.id.in_(ids)))
    )
    return {e.id: CitedEvidence(content=e.content, record_label=record_label(e)) for e in rows}


async def _out(session: AsyncSession, resume: TailoredResume) -> TailoredResumeOut:
    await session.refresh(resume)  # server-set timestamps
    claims = list(
        await session.scalars(
            select(GeneratedClaim)
            .where(GeneratedClaim.tailored_resume_id == resume.id)
            .options(selectinload(GeneratedClaim.verifications))
        )
    )
    in_document = [c for c in claims if c.status != ClaimStatus.REMOVED]
    kept_at = {(c.section, c.position): c.claim_text for c in in_document}
    content = ResumeContent.model_validate(resume.content)
    labels = {f"experience:{e.record_id}": f"Experience · {e.title}" for e in content.experience}
    labels |= {f"projects:{p.record_id}": f"Project · {p.title}" for p in content.projects}
    audit = []
    for claim in (c for c in claims if c.status == ClaimStatus.REMOVED):
        check = claim.verifications[-1] if claim.verifications else None
        rewritten = check is not None and (check.rationale or "").endswith(REWRITTEN)
        audit.append(AuditItem(
            section=labels.get(claim.section or "", (claim.section or "").split(":")[0]),
            original_text=claim.claim_text,
            final_text=kept_at.get((claim.section, claim.position)) if rewritten else None,
            outcome="rewritten" if rewritten else "rejected",
            verdict=check.verdict if check else VerificationVerdict.UNSUPPORTED,
            reason=(check.rationale or "").removesuffix(f" {REWRITTEN}") if check else "",
        ))  # fmt: skip
    reports = await verification.reports_for(session, resume.id)
    report = reports[0] if reports else None
    ids = {i for _, _, claim in content.claims() for i in claim.evidence_ids}
    if report is not None:
        ids |= {i for r in report.claims for i in r.evidence_ids}
    job = await session.get(Job, resume.job_id)
    assert job is not None  # noqa: S101 - FK guarantees the job exists
    return TailoredResumeOut(
        id=resume.id, job_id=resume.job_id, job_title=job.title, company_name=job.company_name,
        version=resume.version, status=resume.status, generator=resume.generator_name,
        created_at=resume.created_at, updated_at=resume.updated_at, content=content,
        verification=VerificationSummary(
            verified_claims=sum(1 for c in in_document if c.status == ClaimStatus.VERIFIED),
            rewritten=sum(1 for a in audit if a.outcome == "rewritten"),
            rejected=sum(1 for a in audit if a.outcome == "rejected"),
            audit=audit,
        ),
        notes=resume.notes, evidence=await _evidence(session, ids), report=report,
    )  # fmt: skip


# --- Persistence ------------------------------------------------------------------------


def _method(result_method: str) -> VerificationMethod:
    return VerificationMethod.LLM if result_method == "llm" else VerificationMethod.RULE_BASED


async def _store_claims(
    session: AsyncSession,
    resume: TailoredResume,
    content: ResumeContent,
    run: EngineRun,
    outcomes: list[Outcome],
    draft_log_id: uuid.UUID | None = None,
) -> None:
    """Store the document's claims with the engine's verdicts, plus the removed originals."""
    results = {(r.section, r.position): r for r in run.report.claims if r.claim_type in (
        ClaimType.SUMMARY, ClaimType.SKILL, ClaimType.EXPERIENCE, ClaimType.PROJECT)}  # fmt: skip
    for section, position, claim in content.claims():
        result = results[(section, position)]
        # Only approved claims link evidence (which makes that evidence undeletable).
        linked = [await session.get(CandidateEvidence, i) for i in result.evidence_ids] if (
            result.approved) else []  # fmt: skip
        record = GeneratedClaim(
            tailored_resume_id=resume.id, claim_text=claim.text, section=section,
            position=position,
            status=ClaimStatus.VERIFIED if result.approved else ClaimStatus.UNSUPPORTED,
            evidence=[e for e in linked if e is not None],
        )  # fmt: skip
        record.verifications.append(ClaimVerification(
            verdict=result.verification_status, method=_method(result.method),
            confidence=result.confidence, rationale=result.reason,
            # A result reused from the draft pass carries that pass's AI call.
            ai_execution_log_id=(run.ai_execution_log_id or draft_log_id)
            if result.method == "llm" else None,
        ))  # fmt: skip
        session.add(record)
        await session.flush()
        claim.claim_id = record.id
    for outcome in outcomes:
        # Kept for audit only; never linked to evidence.
        removed = GeneratedClaim(
            tailored_resume_id=resume.id, claim_text=outcome.original[:5000].strip() or "(blank)",
            section=outcome.section[:50], position=max(outcome.position, 0),
            status=ClaimStatus.REMOVED,
        )  # fmt: skip
        removed.verifications.append(ClaimVerification(
            verdict=outcome.verdict, method=_method(outcome.method),
            confidence=outcome.confidence,
            rationale=f"{outcome.reason} {REWRITTEN}" if outcome.final else outcome.reason,
            ai_execution_log_id=draft_log_id if outcome.method == "llm" else None,
        ))  # fmt: skip
        session.add(removed)


def _status(run: EngineRun) -> DocumentStatus:
    return DocumentStatus.VERIFIED if run.report.outcome == "approved" else (
        DocumentStatus.VERIFICATION_FAILED)  # fmt: skip


async def generate(
    session: AsyncSession,
    user: User,
    job_id: uuid.UUID,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
    match_llm: LLMProvider | None = None,
    verify_llm: LLMProvider | None = None,
) -> TailoredResumeOut:
    """``llm`` words the resume; ``match_llm`` refreshes a missing or stale match report;
    ``verify_llm`` is the verification engine's optional reviewer."""
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    match_report = await _current_report(session, user, job, embedder, match_llm, settings)
    ws = await load_workspace(session, profile.id, job, match_report, embedder)

    generator_name, log_id = RuleGenerator.name, None
    draft = RuleGenerator().generate(ws)
    if settings.resume_generator != "rules" and llm is not None:
        generator = LLMGenerator(llm)
        log = AIExecutionLog(
            user_id=user.id, operation=AIOperation.RESUME_GENERATION, provider=llm.name,
            model=llm.model, prompt_version=PROMPT_VERSION,
        )  # fmt: skip
        try:
            draft, raw = await generator.generate(ws)
        except LLMError as exc:
            log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
            draft.notes.append(f"AI tailoring failed ({exc}); rule-based tailoring was used.")
        else:
            log.status, log.model, generator_name = (
                AIExecutionStatus.SUCCESS,
                raw.model,
                generator.name,
            )
            log.input_tokens, log.output_tokens = raw.input_tokens, raw.output_tokens
            log.latency_ms = raw.latency_ms
        session.add(log)
        await session.flush()
        log_id = log.id
    elif settings.resume_generator == "llm":
        draft.notes.append(
            "AI tailoring is not configured (set ANTHROPIC_API_KEY); rule-based tailoring was used."
        )

    # Claim extraction and verification of the draft, then the rewrite-or-remove step.
    knowledge = await load_knowledge(session, profile.id)
    draft_claims = extract_resume_claims(draft.content)
    draft_run = await verify_claims(session, user, knowledge, draft_claims, embedder,
                                    verify_llm, settings)  # fmt: skip
    content, outcomes, recited = apply_verdicts(
        draft.content, draft_claims, draft_run.report.claims, knowledge
    )
    outcomes[:0] = [
        Outcome(section, -1, text, None, VerificationVerdict.UNSUPPORTED, why, 0.0)
        for section, text, why in draft.prefiltered
    ]
    # The final resume is verified again, as a whole: that verdict decides its status.
    final_claims = extract_resume_claims(content)
    final_run = await verify_claims(
        session, user, knowledge, final_claims, embedder, verify_llm, settings,
        reuse=list(zip(draft_claims, draft_run.report.claims, strict=True)),
    )  # fmt: skip

    version = (await session.scalar(
        select(func.max(TailoredResume.version)).where(
            TailoredResume.candidate_profile_id == profile.id, TailoredResume.job_id == job.id)
    ) or 0) + 1  # fmt: skip
    # Superseded, unapproved versions are replaced (which also releases their evidence).
    await session.execute(
        delete(TailoredResume).where(
            TailoredResume.candidate_profile_id == profile.id, TailoredResume.job_id == job.id,
            TailoredResume.status != DocumentStatus.APPROVED,
        )
    )  # fmt: skip
    primary = await session.scalar(
        select(Resume.id).where(Resume.candidate_profile_id == profile.id, Resume.is_primary)
    )
    resume = TailoredResume(
        candidate_profile_id=profile.id, job_id=job.id, base_resume_id=primary, version=version,
        status=_status(final_run), content={}, generator_name=generator_name,
        notes=[*draft.notes, *recited, *final_run.report.warnings], ai_execution_log_id=log_id,
    )  # fmt: skip
    session.add(resume)
    await session.flush()
    await _store_claims(
        session, resume, content, final_run, outcomes, draft_run.ai_execution_log_id
    )
    resume.content = content.model_dump(mode="json")
    await verification.store_report(
        session, final_run.report, tailored_resume_id=resume.id,
        trigger=VerificationTrigger.GENERATION,
        ai_execution_log_id=final_run.ai_execution_log_id or draft_run.ai_execution_log_id,
    )  # fmt: skip
    await session.commit()
    return await _out(session, resume)


async def _owned_resume(session: AsyncSession, user: User, resume_id: uuid.UUID) -> TailoredResume:
    profile = await profiles.get_profile(session, user)
    resume = await session.scalar(
        select(TailoredResume)
        .where(TailoredResume.id == resume_id, TailoredResume.candidate_profile_id == profile.id)
        .execution_options(populate_existing=True)
    )
    if resume is None:
        raise NotFoundError("Tailored resume not found.")
    return resume


async def latest(session: AsyncSession, user: User, job_id: uuid.UUID) -> TailoredResumeOut:
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    resume = await session.scalar(
        select(TailoredResume)
        .where(TailoredResume.candidate_profile_id == profile.id, TailoredResume.job_id == job.id)
        .order_by(TailoredResume.version.desc())
        .limit(1)
    )
    if resume is None:
        raise NotFoundError("No tailored resume for this job yet.")
    return await _out(session, resume)


async def get(session: AsyncSession, user: User, resume_id: uuid.UUID) -> TailoredResume:
    return await _owned_resume(session, user, resume_id)


# --- Editing and re-verification --------------------------------------------------------


def _rejection(result: ClaimResult) -> str:
    return (
        f"{STATUS_LABELS[result.verification_status]}: “{result.claim_text[:80]}”. {result.reason}"
    )


async def update(
    session: AsyncSession,
    user: User,
    resume_id: uuid.UUID,
    edited: ResumeContent,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> TailoredResumeOut:
    """Save the candidate's edits, only if the engine approves every claim in them.

    Record facts (names, titles, employers, dates) are verified like everything else: an
    edit that changes one is CONTRADICTED and rejected, not silently corrected.
    """
    resume = await _owned_resume(session, user, resume_id)
    if resume.status == DocumentStatus.APPROVED:
        raise ConflictError("Approved resumes can't be edited; regenerate to make changes.")
    knowledge = await load_knowledge(session, resume.candidate_profile_id)
    run = await verify_claims(session, user, knowledge, extract_resume_claims(edited), embedder,
                              llm, settings)  # fmt: skip
    errors = {r.key: _rejection(r) for r in run.report.claims if not r.approved}
    if errors:
        await session.commit()  # keeps the AI execution log, if any
        raise FieldErrors(errors)

    content = edited.model_copy(deep=True)
    await session.execute(
        delete(GeneratedClaim).where(GeneratedClaim.tailored_resume_id == resume.id)
    )
    await _store_claims(session, resume, content, run, [])
    resume.content = content.model_dump(mode="json")
    resume.status = DocumentStatus.VERIFIED
    await verification.store_report(
        session, run.report, tailored_resume_id=resume.id, trigger=VerificationTrigger.EDIT,
        ai_execution_log_id=run.ai_execution_log_id,
    )  # fmt: skip
    await session.commit()
    return await _out(session, await _owned_resume(session, user, resume_id))


async def reverify(
    session: AsyncSession,
    user: User,
    resume_id: uuid.UUID,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> TailoredResumeOut:
    """Verify a stored resume again (e.g. after the profile changed) and record the result.

    Claims that no longer pass are marked unsupported and the resume becomes
    VERIFICATION_FAILED; nothing in it is changed or fixed automatically.
    """
    resume = await _owned_resume(session, user, resume_id)
    content = ResumeContent.model_validate(resume.content)
    knowledge = await load_knowledge(session, resume.candidate_profile_id)
    run = await verify_claims(session, user, knowledge, extract_resume_claims(content), embedder,
                              llm, settings)  # fmt: skip
    results = {(r.section, r.position): r for r in run.report.claims}
    stored = await session.scalars(
        select(GeneratedClaim).where(
            GeneratedClaim.tailored_resume_id == resume.id,
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
    if resume.status != DocumentStatus.APPROVED:
        resume.status = _status(run)
    await verification.store_report(
        session, run.report, tailored_resume_id=resume.id, trigger=VerificationTrigger.MANUAL,
        ai_execution_log_id=run.ai_execution_log_id,
    )  # fmt: skip
    await session.commit()
    return await _out(session, await _owned_resume(session, user, resume_id))


async def output(session: AsyncSession, resume: TailoredResume) -> TailoredResumeOut:
    return await _out(session, resume)


def as_content(resume: TailoredResume) -> ResumeContent:
    return ResumeContent.model_validate(resume.content)


async def delete_resume(session: AsyncSession, user: User, resume_id: uuid.UUID) -> None:
    resume = await _owned_resume(session, user, resume_id)
    if resume.status == DocumentStatus.APPROVED:
        raise ConflictError("Approved resumes can't be deleted.")
    await session.delete(resume)  # its claims, links, verifications and reports cascade
    await session.commit()
