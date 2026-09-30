"""Candidate-job matching: compute, store, and report requirement-level matches.

Pipeline per requirement (informational statements aren't requirements and aren't scored):
semantic retrieval of *verified* evidence -> structured checks -> judge (rules, or an LLM
grounded against the retrieved evidence) -> explainable scoring.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.embeddings import EmbeddingProvider
from app.ai.models import AIExecutionLog, AIExecutionStatus, AIOperation
from app.ai.provider import LLMError, LLMProvider
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.jobs.analysis.vocabulary import find_technologies
from app.jobs.models import Job, JobRequirement, RequirementImportance, RequirementType
from app.matching.engine.facts import CandidateFacts, load_facts
from app.matching.engine.judge import Assessment, RuleJudge
from app.matching.engine.llm_judge import PROMPT_VERSION, LLMJudge
from app.matching.engine.scoring import DISCLAIMER, SCORING_VERSION, SKILL_TYPES, score
from app.matching.models import (
    GapSeverity,
    JobMatch,
    MatchStatus,
    RequirementMatch,
    SkillGap,
    requirement_match_evidence,
)
from app.matching.schemas import MatchingEvidence, MatchReportOut, RequirementMatchOut, ScoresOut
from app.profiles import service as profiles
from app.profiles.models import CandidateEvidence
from app.retrieval.schemas import RetrievedEvidence
from app.retrieval.service import evidence_source, retrieve_verified_for_queries, with_sources
from app.users.models import User

TOP_K = 5
STRONGEST = 5


async def _owned_job(session: AsyncSession, user: User, job_id: uuid.UUID) -> Job:
    job = await session.scalar(
        select(Job)
        .where(Job.id == job_id, Job.created_by_user_id == user.id)
        .options(selectinload(Job.requirements))
    )
    if job is None:
        raise NotFoundError("Job not found.")
    return job


def _query(requirement: JobRequirement) -> str:
    if requirement.requirement_type == RequirementType.TECHNOLOGY:
        return f"Experience with {requirement.description}"
    return requirement.description


async def _assess(
    session: AsyncSession,
    user: User,
    requirements: list[JobRequirement],
    retrieved: list[list[RetrievedEvidence]],
    facts: CandidateFacts,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> tuple[list[Assessment], str, list[str]]:
    rules = RuleJudge(embedder.confidence_thresholds)
    rule_results = [
        rules.assess(r, ev, facts) for r, ev in zip(requirements, retrieved, strict=True)
    ]
    warnings: list[str] = []
    if settings.match_judge != "rules" and llm is not None and requirements:
        judge = LLMJudge(llm)
        log = AIExecutionLog(
            user_id=user.id,
            operation=AIOperation.MATCHING,
            provider=llm.name,
            model=llm.model,
            prompt_version=PROMPT_VERSION,
        )
        try:
            results, raw = await judge.assess(
                list(zip(requirements, retrieved, rule_results, strict=True))
            )
        except LLMError as exc:
            log.status, log.error_message = AIExecutionStatus.ERROR, str(exc)
            session.add(log)
            warnings.append(f"AI matching failed ({exc}); rule-based matching was used.")
        else:
            log.status, log.model = AIExecutionStatus.SUCCESS, raw.model
            log.input_tokens, log.output_tokens = raw.input_tokens, raw.output_tokens
            log.latency_ms = raw.latency_ms
            session.add(log)
            return results, judge.name, warnings
    elif settings.match_judge == "llm":
        warnings.append(
            "The AI judge is not configured (set ANTHROPIC_API_KEY); rule-based matching was used."
        )
    return rule_results, rules.name, warnings


def _gap(requirement: JobRequirement, a: Assessment) -> tuple[GapSeverity, str] | None:
    required = requirement.importance == RequirementImportance.REQUIRED
    if a.status == MatchStatus.MISSING:
        if required and requirement.requirement_type == RequirementType.ELIGIBILITY:
            return GapSeverity.BLOCKING, "This eligibility condition doesn't appear to be met."
        if required:
            return GapSeverity.SIGNIFICANT, (
                "If you have this, add a highlight that shows it; otherwise it's a gap to address."
            )
        return GapSeverity.MINOR, "A preferred qualification; worth adding if you have it."
    if a.status == MatchStatus.PARTIAL:
        return (
            GapSeverity.MINOR,
            "Strengthen this with a highlight that directly shows it."
            if a.basis != "none"
            else "Partially covered.",
        )
    return None


async def compute_match(
    session: AsyncSession,
    user: User,
    job_id: uuid.UUID,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    settings: Settings,
) -> MatchReportOut:
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    requirements = sorted(
        (r for r in job.requirements if r.importance != RequirementImportance.INFORMATIONAL),
        key=lambda r: r.sort_order,
    )
    facts = await load_facts(session, profile.id)
    retrieved = await retrieve_verified_for_queries(
        session, profile.id, [_query(r) for r in requirements], embedder, top_k=TOP_K
    )
    assessments, matcher, warnings = await _assess(
        session, user, requirements, retrieved, facts, embedder, llm, settings
    )
    if facts.verified_evidence_count == 0:
        warnings.append(
            "Your profile has no verified evidence yet. Add highlights to your "
            "projects and experience to get meaningful matches."
        )

    pairs = list(zip(requirements, assessments, strict=True))
    scores = score(pairs)
    counts = {s: sum(1 for _, a in pairs if a.status == s) for s in MatchStatus}
    await session.execute(
        delete(JobMatch).where(
            JobMatch.candidate_profile_id == profile.id, JobMatch.job_id == job.id
        )
    )
    match = JobMatch(
        candidate_profile_id=profile.id,
        job_id=job.id,
        overall_score=scores.overall,
        semantic_score=scores.semantic,
        skill_score=scores.skill,
        required_coverage=scores.required_coverage,
        preferred_coverage=scores.preferred_coverage,
        summary=(
            f"{counts[MatchStatus.MATCHED]} matched, {counts[MatchStatus.PARTIAL]} partial, "
            f"{counts[MatchStatus.MISSING]} missing, {counts[MatchStatus.UNKNOWN]} could not "
            f"be assessed, out of {len(pairs)} requirements."
        ),
        scoring_version=SCORING_VERSION,
        matcher_name=matcher,
        embedding_model=embedder.model,
        inputs_fingerprint=facts.fingerprint,
        warnings=warnings,
    )
    session.add(match)
    await session.flush()

    similarity = {(i, e.evidence_id): e.similarity for i, ev in enumerate(retrieved) for e in ev}
    for index, (requirement, a) in enumerate(pairs):
        row = RequirementMatch(
            job_match_id=match.id,
            job_requirement_id=requirement.id,
            status=a.status,
            semantic_similarity=a.semantic_similarity,
            explanation=a.explanation,
            judge=a.judge,
            details={**a.details, "basis": a.basis},
        )
        session.add(row)
        await session.flush()
        if a.evidence_ids:
            await session.execute(
                insert(requirement_match_evidence),
                [
                    {
                        "requirement_match_id": row.id,
                        "evidence_id": evidence_id,
                        "similarity": similarity.get((index, evidence_id)),
                        "rank": rank,
                    }
                    for rank, evidence_id in enumerate(dict.fromkeys(a.evidence_ids))
                ],
            )
        if gap := _gap(requirement, a):
            session.add(
                SkillGap(
                    job_match_id=match.id,
                    job_requirement_id=requirement.id,
                    skill_id=requirement.skill_id,
                    severity=gap[0],
                    description=a.explanation,
                    recommendation=gap[1],
                )
            )
    await session.commit()
    return await get_report(session, user, job.id)


async def get_report(session: AsyncSession, user: User, job_id: uuid.UUID) -> MatchReportOut:
    profile = await profiles.get_profile(session, user)
    job = await _owned_job(session, user, job_id)
    match = await session.scalar(
        select(JobMatch)
        .where(JobMatch.candidate_profile_id == profile.id, JobMatch.job_id == job.id)
        .options(
            selectinload(JobMatch.requirement_matches).selectinload(
                RequirementMatch.job_requirement
            )
        )
        .execution_options(populate_existing=True)
    )
    if match is None:
        raise NotFoundError("This job hasn't been matched against your profile yet.")

    links = (
        await session.execute(
            select(requirement_match_evidence).where(
                requirement_match_evidence.c.requirement_match_id.in_(
                    [r.id for r in match.requirement_matches]
                )
            )
        )
    ).all()
    evidence_ids = {link.evidence_id for link in links}
    evidence = {
        e.id: e
        for e in await session.scalars(
            with_sources(select(CandidateEvidence).where(CandidateEvidence.id.in_(evidence_ids)))
        )
    }
    by_match: dict[uuid.UUID, list[MatchingEvidence]] = {}
    for link in sorted(links, key=lambda link: link.rank):
        if (e := evidence.get(link.evidence_id)) is not None:
            by_match.setdefault(link.requirement_match_id, []).append(
                MatchingEvidence(
                    evidence_id=e.id,
                    factual_content=e.content,
                    similarity=link.similarity,
                    source=evidence_source(e),
                )
            )

    rows = sorted(match.requirement_matches, key=lambda r: r.job_requirement.sort_order)
    items = [
        RequirementMatchOut(
            requirement_id=r.job_requirement_id,
            requirement=r.job_requirement.description,
            requirement_type=r.job_requirement.requirement_type,
            importance=r.job_requirement.importance,
            matching_candidate_evidence=by_match.get(r.id, []),
            semantic_similarity=r.semantic_similarity,
            match_status=r.status,
            explanation=r.explanation,
            evidence_ids=[m.evidence_id for m in by_match.get(r.id, [])],
            judge=r.judge,
            details=r.details,
        )
        for r in rows
    ]
    required = RequirementImportance.REQUIRED
    matched = [i for i in items if i.match_status == MatchStatus.MATCHED]
    strongest = sorted(
        matched, key=lambda i: (i.importance != required, -(i.semantic_similarity or 0))
    )
    facts = await load_facts(session, profile.id)
    informational = await session.scalar(
        select(func.count())
        .select_from(JobRequirement)
        .where(
            JobRequirement.job_id == job.id,
            JobRequirement.importance == RequirementImportance.INFORMATIONAL,
        )
    )
    return MatchReportOut(
        job_id=job.id,
        job_title=job.title,
        company_name=job.company_name,
        candidate_id=profile.id,
        computed_at=_computed_at(match),
        matcher=match.matcher_name,
        embedding_model=match.embedding_model,
        scoring_version=match.scoring_version,
        disclaimer=DISCLAIMER,
        summary=match.summary,
        scores=ScoresOut(
            evidence_coverage=match.overall_score,
            required_coverage=match.required_coverage,
            preferred_coverage=match.preferred_coverage,
            semantic_similarity=match.semantic_score,
            skill_coverage=match.skill_score,
        ),
        status_counts={s: sum(1 for i in items if i.match_status == s) for s in MatchStatus},
        requirements=items,
        strongest_matches=strongest[:STRONGEST],
        missing_skills=_missing_skills(items),
        partial_matches=[i for i in items if i.match_status == MatchStatus.PARTIAL],
        potentially_disqualifying=[
            i
            for i in items
            if i.requirement_type == RequirementType.ELIGIBILITY
            and i.importance == required
            and i.match_status in (MatchStatus.MISSING, MatchStatus.UNKNOWN)
        ],
        informational_not_scored=informational or 0,
        warnings=match.warnings,
        is_stale=match.inputs_fingerprint != facts.fingerprint,
    )


def _missing_skills(items: list[RequirementMatchOut]) -> list[RequirementMatchOut]:
    """Missing skills, without repeating a statement whose technologies are listed anyway
    ("Familiarity with Terraform." next to "Terraform")."""
    missing = [
        i
        for i in items
        if i.match_status == MatchStatus.MISSING and i.requirement_type in SKILL_TYPES
    ]
    named = {i.requirement for i in missing if i.requirement_type == RequirementType.TECHNOLOGY}
    return [
        i
        for i in missing
        if i.requirement_type == RequirementType.TECHNOLOGY
        or not (
            set(find_technologies(i.requirement)) and set(find_technologies(i.requirement)) <= named
        )
    ]


def _computed_at(match: JobMatch) -> datetime:
    return match.updated_at or match.created_at
