"""Tailored resume pipeline (claim-first):

evidence retrieval -> candidate claims -> resume generation -> claim extraction ->
claim verification -> final resume.

Unsupported claims are rewritten to the evidence they cite (verbatim, so true by
construction) or rejected; every decision is stored with its reason. Tailored resumes live
in ``tailored_resumes``, separate from the uploaded master resumes.
"""

import uuid
from dataclasses import dataclass
from typing import Any

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
from app.documents.resume.content import (
    Claim,
    ExperienceEntry,
    ProjectEntry,
    ResumeContent,
)
from app.documents.resume.generator import (
    PROMPT_VERSION,
    Draft,
    LLMGenerator,
    RuleGenerator,
    achievement_of,
    certification_of,
    coursework_of,
    education_of,
    experience_of,
    header_of,
    project_of,
)
from app.documents.resume.schemas import (
    AuditItem,
    CitedEvidence,
    TailoredResumeOut,
    VerificationSummary,
)
from app.documents.resume.verifier import ClaimKind, EvidenceText, Verification, verify_claim
from app.documents.resume.workspace import EvidenceItem, Workspace, load_workspace
from app.jobs.models import Job
from app.matching import service as matching
from app.matching.schemas import MatchReportOut
from app.profiles import service as profiles
from app.profiles.models import (
    EVIDENCE_SUBJECT_COLUMNS,
    CandidateEvidence,
    CandidateProfile,
    Resume,
    VerificationStatus,
)
from app.retrieval.service import record_label, with_sources
from app.users.models import User

REWRITTEN = "Rewritten to the cited evidence."


@dataclass
class Outcome:
    section: str
    position: int
    original: str
    final: Claim | None
    verification: Verification


# --- Verification -----------------------------------------------------------------------


def _texts(ids: list[uuid.UUID], evidence: dict[uuid.UUID, EvidenceItem]) -> list[EvidenceText]:
    return [EvidenceText(evidence[i].content, evidence[i].context) for i in ids if i in evidence]


def _check(claim: Claim, kind: ClaimKind, allowed: dict[uuid.UUID, EvidenceItem]) -> Verification:
    return verify_claim(claim.text, _texts(claim.evidence_ids, allowed), kind)


def verify_draft(draft: Draft, ws: Workspace) -> tuple[ResumeContent, list[Outcome]]:
    """Keep supported claims; rewrite or reject the rest. Returns the final content."""
    content = draft.content.model_copy(deep=True)
    outcomes: list[Outcome] = [
        Outcome(section, -1, text, None, Verification(VerificationVerdict.UNSUPPORTED, why, 0.0))
        for section, text, why in draft.prefiltered
    ]

    def keep(section: str, claims: list[Claim], kind: ClaimKind,
             allowed: dict[uuid.UUID, EvidenceItem]) -> list[Claim]:  # fmt: skip
        kept: list[Claim] = []
        for claim in claims:
            claim.evidence_ids = [i for i in dict.fromkeys(claim.evidence_ids) if i in allowed]
            result = _check(claim, kind, allowed)
            if result.supported:
                final: Claim | None = claim
            elif kind == ClaimKind.BULLET and claim.evidence_ids:
                source = allowed[claim.evidence_ids[0]]  # rewrite: the evidence itself
                final = Claim(text=source.content, evidence_ids=[source.id])
            else:
                final = None
            if final is not None and all(k.text != final.text for k in kept):  # no duplicates
                kept.append(final)
            if not result.supported:
                # A rewrite records where its replacement ended up, so the audit can show it.
                at = next(i for i, k in enumerate(kept) if k.text == final.text) if final else -1
                outcomes.append(Outcome(section, at, claim.text, final, result))
        return kept

    content.summary = keep("summary", content.summary, ClaimKind.SUMMARY, ws.evidence)
    content.skills = keep("skills", content.skills, ClaimKind.SKILL, ws.evidence)
    entries: list[tuple[str, ExperienceEntry | ProjectEntry]] = [
        *(("experience", e) for e in content.experience),
        *(("projects", p) for p in content.projects),
    ]
    for kind, entry in entries:
        own = {e.id: e for e in ws.by_subject.get(entry.record_id, [])}
        entry.bullets = keep(f"{kind}:{entry.record_id}", entry.bullets, ClaimKind.BULLET, own)
    return content, outcomes


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


async def _cited_evidence(
    session: AsyncSession, content: ResumeContent
) -> dict[uuid.UUID, CitedEvidence]:
    ids = {i for _, _, claim in content.claims() for i in claim.evidence_ids}
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
    verified = [c for c in claims if c.status == ClaimStatus.VERIFIED]
    kept_at = {(c.section, c.position): c.claim_text for c in verified}
    content = ResumeContent.model_validate(resume.content)
    labels = {
        f"experience:{e.record_id}": f"Experience \u00b7 {e.title}" for e in content.experience
    }
    labels |= {f"projects:{p.record_id}": f"Project \u00b7 {p.title}" for p in content.projects}
    audit = []
    for claim in (c for c in claims if c.status == ClaimStatus.UNSUPPORTED):
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
    job = await session.get(Job, resume.job_id)
    assert job is not None  # noqa: S101 - FK guarantees the job exists
    return TailoredResumeOut(
        id=resume.id, job_id=resume.job_id, job_title=job.title, company_name=job.company_name,
        version=resume.version, status=resume.status, generator=resume.generator_name,
        created_at=resume.created_at, updated_at=resume.updated_at, content=content,
        verification=VerificationSummary(
            verified_claims=len(verified),
            rewritten=sum(1 for a in audit if a.outcome == "rewritten"),
            rejected=sum(1 for a in audit if a.outcome == "rejected"),
            audit=audit,
        ),
        notes=resume.notes, evidence=await _cited_evidence(session, content),
    )  # fmt: skip


# --- Persistence ------------------------------------------------------------------------


async def _store_claims(
    session: AsyncSession, resume: TailoredResume, content: ResumeContent, outcomes: list[Outcome]
) -> None:
    for section, position, claim in content.claims():
        evidence = [await session.get(CandidateEvidence, i) for i in claim.evidence_ids]
        record = GeneratedClaim(
            tailored_resume_id=resume.id, claim_text=claim.text, section=section,
            position=position, status=ClaimStatus.VERIFIED,
            evidence=[e for e in evidence if e is not None],
        )  # fmt: skip
        record.verifications.append(ClaimVerification(
            verdict=VerificationVerdict.SUPPORTED, method=VerificationMethod.RULE_BASED,
            confidence=1.0, rationale="Supported by the cited evidence.",
        ))  # fmt: skip
        session.add(record)
        await session.flush()
        claim.claim_id = record.id
    for outcome in outcomes:
        # Not linked to evidence: a rejected claim must not make evidence look "cited".
        rejected = GeneratedClaim(
            tailored_resume_id=resume.id, claim_text=outcome.original[:5000].strip() or "(blank)",
            section=outcome.section[:50], position=max(outcome.position, 0),
            status=ClaimStatus.UNSUPPORTED,
        )  # fmt: skip
        rationale = outcome.verification.rationale
        rejected.verifications.append(ClaimVerification(
            verdict=outcome.verification.verdict, method=VerificationMethod.RULE_BASED,
            confidence=outcome.verification.confidence,
            rationale=f"{rationale} {REWRITTEN}" if outcome.final else rationale,
        ))  # fmt: skip
        session.add(rejected)


async def generate(
    session: AsyncSession,
    user: User,
    job_id: uuid.UUID,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
    match_llm: LLMProvider | None = None,
) -> TailoredResumeOut:
    """``llm`` words the resume; ``match_llm`` refreshes a missing or stale match report."""
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    report = await _current_report(session, user, job, embedder, match_llm, settings)
    ws = await load_workspace(session, profile.id, job, report, embedder)

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

    content, outcomes = verify_draft(draft, ws)
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
        status=DocumentStatus.VERIFIED, content={}, generator_name=generator_name,
        notes=draft.notes, ai_execution_log_id=log_id,
    )  # fmt: skip
    session.add(resume)
    await session.flush()
    await _store_claims(session, resume, content, outcomes)
    resume.content = content.model_dump(mode="json")
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


# --- Editing ----------------------------------------------------------------------------


async def _evidence_map(
    session: AsyncSession, profile_id: uuid.UUID
) -> dict[uuid.UUID, EvidenceItem]:
    rows = await session.scalars(
        with_sources(select(CandidateEvidence).where(
            CandidateEvidence.candidate_profile_id == profile_id,
            CandidateEvidence.verification_status == VerificationStatus.VERIFIED,
        ))
    )  # fmt: skip
    items = {}
    for e in rows:
        column = EVIDENCE_SUBJECT_COLUMNS.get(e.source_type)
        items[e.id] = EvidenceItem(e.id, e.content, record_label(e) or "",
                                   getattr(e, column) if column else None, 0.0)  # fmt: skip
    return items


def _record_facts(content: ResumeContent, profile: CandidateProfile) -> ResumeContent:
    """Re-derive every record fact from the profile, so edits can't change them."""
    jobs = {j.id: j for j in profile.work_experiences}
    projects = {p.id: p for p in profile.projects}
    educations = {e.id: e for e in profile.educations}
    certs = {c.id: c for c in profile.certifications}
    wins = {a.id: a for a in profile.achievements}
    courses = {c.id: c for c in profile.coursework}
    errors: dict[str, str] = {}

    def need(kind: str, known: dict[uuid.UUID, Any], record_id: uuid.UUID) -> Any:
        if record_id not in known:
            errors[f"{kind}.{record_id}"] = f"Unknown {kind} entry; it isn't in your profile."
        return known.get(record_id)

    rebuilt = ResumeContent(
        header=header_of(profile),
        summary=content.summary,
        skills=content.skills,
        experience=[
            experience_of(j, x.bullets)
            for x in content.experience
            if (j := need("experience", jobs, x.record_id))
        ],
        projects=[
            project_of(pr, x.bullets)
            for x in content.projects
            if (pr := need("project", projects, x.record_id))
        ],
        education=[
            education_of(ed)
            for x in content.education
            if (ed := need("education", educations, x.record_id))
        ],
        certifications=[
            certification_of(ce)
            for x in content.certifications
            if (ce := need("certification", certs, x.record_id))
        ],
        achievements=[
            achievement_of(ac)
            for x in content.achievements
            if (ac := need("achievement", wins, x.record_id))
        ],
        coursework=[
            coursework_of(co)
            for x in content.coursework
            if (co := need("coursework", courses, x.record_id))
        ],
    )
    if errors:
        raise FieldErrors(errors)
    return rebuilt


async def update(
    session: AsyncSession, user: User, resume_id: uuid.UUID, edited: ResumeContent
) -> TailoredResumeOut:
    """Save the candidate's edits. Every claim is re-verified; nothing unsupported is saved."""
    resume = await _owned_resume(session, user, resume_id)
    if resume.status == DocumentStatus.APPROVED:
        raise ConflictError("Approved resumes can't be edited; regenerate to make changes.")
    profile = await session.scalar(
        select(CandidateProfile).where(CandidateProfile.id == resume.candidate_profile_id).options(
            *(selectinload(getattr(CandidateProfile, a)) for a in (
                "work_experiences", "projects", "educations", "certifications", "achievements",
                "coursework")))
    )  # fmt: skip
    assert profile is not None  # noqa: S101 - FK guarantees the profile exists
    content = _record_facts(edited, profile)
    evidence = await _evidence_map(session, profile.id)

    errors: dict[str, str] = {}
    for section, position, claim in content.claims():
        if section == "summary" or section == "skills":
            allowed, kind = evidence, ClaimKind.SUMMARY if section == "summary" else ClaimKind.SKILL
        else:
            record_id = uuid.UUID(section.split(":")[1])
            allowed = {i: e for i, e in evidence.items() if e.subject_id == record_id}
            kind = ClaimKind.BULLET
        unknown = [i for i in claim.evidence_ids if i not in allowed]
        if unknown or not claim.evidence_ids:
            errors[f"{section}[{position}]"] = (
                "Cites evidence that isn't this item's verified evidence." if unknown
                else "Every statement must cite at least one piece of your evidence."
            )  # fmt: skip
            continue
        result = verify_claim(claim.text, _texts(claim.evidence_ids, allowed), kind)
        if not result.supported:
            errors[f"{section}[{position}]"] = (
                f"\u201c{claim.text[:80]}\u201d isn't supported: {result.rationale} "
                "Add the fact as a highlight in your profile first, or keep to what it says."
            )
    if errors:
        raise FieldErrors(errors)

    await session.execute(
        delete(GeneratedClaim).where(GeneratedClaim.tailored_resume_id == resume.id)
    )
    await _store_claims(session, resume, content, [])
    resume.content = content.model_dump(mode="json")
    resume.status = DocumentStatus.VERIFIED
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
    await session.delete(resume)  # its claims, links and verifications cascade
    await session.commit()
