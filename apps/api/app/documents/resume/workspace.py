"""Everything resume generation may use: the candidate's profile records, their verified
evidence, and how relevant each is to the job (the evidence-retrieval step)."""

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.embeddings import EmbeddingProvider
from app.jobs.analysis.vocabulary import find_technologies
from app.jobs.models import Job, RequirementImportance, RequirementType
from app.matching.models import MatchStatus
from app.matching.schemas import MatchReportOut
from app.profiles.models import (
    EVIDENCE_SUBJECT_COLUMNS,
    CandidateEvidence,
    CandidateProfile,
    CandidateSkill,
    VerificationStatus,
)
from app.retrieval.service import index_evidence, record_label, with_sources

MATCH_BOOST = 0.5


@dataclass(frozen=True)
class EvidenceItem:
    id: uuid.UUID
    content: str
    context: str  # the owning record, e.g. "Machine Learning Intern at Acme"
    subject_id: uuid.UUID | None
    relevance: float


@dataclass
class Workspace:
    profile: CandidateProfile
    job: Job
    evidence: dict[uuid.UUID, EvidenceItem] = field(default_factory=dict)
    by_subject: dict[uuid.UUID, list[EvidenceItem]] = field(default_factory=dict)
    record_relevance: dict[uuid.UUID, float] = field(default_factory=dict)
    skill_evidence: dict[str, list[EvidenceItem]] = field(default_factory=dict)  # by skill name
    required_concepts: list[str] = field(default_factory=list)
    preferred_concepts: list[str] = field(default_factory=list)

    def evidence_for(self, record_id: uuid.UUID) -> list[EvidenceItem]:
        return sorted(self.by_subject.get(record_id, []), key=lambda e: -e.relevance)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def job_query(job: Job) -> str:
    stated = [
        r.description
        for r in sorted(job.requirements, key=lambda r: r.sort_order)
        if r.importance != RequirementImportance.INFORMATIONAL
    ]
    return f"{job.title}. " + "; ".join(stated)[:6000]


def _match_boost(report: MatchReportOut | None) -> dict[uuid.UUID, float]:
    boost: dict[uuid.UUID, float] = defaultdict(float)
    if report is None:
        return boost
    for item in report.requirements:
        weight = 1.0 if item.importance == RequirementImportance.REQUIRED else 0.5
        credit = {MatchStatus.MATCHED: 1.0, MatchStatus.PARTIAL: 0.5}.get(item.match_status, 0.0)
        for evidence_id in item.evidence_ids:
            boost[evidence_id] += weight * credit
    top = max(boost.values(), default=0.0)
    return {k: v / top for k, v in boost.items()} if top else boost


async def load_workspace(
    session: AsyncSession,
    profile_id: uuid.UUID,
    job: Job,
    report: MatchReportOut | None,
    embedder: EmbeddingProvider,
) -> Workspace:
    profile = await session.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.id == profile_id)
        .options(
            selectinload(CandidateProfile.work_experiences),
            selectinload(CandidateProfile.projects),
            selectinload(CandidateProfile.educations),
            selectinload(CandidateProfile.certifications),
            selectinload(CandidateProfile.achievements),
            selectinload(CandidateProfile.coursework),
            selectinload(CandidateProfile.skills).selectinload(CandidateSkill.skill),
        )
        .execution_options(populate_existing=True)
    )
    if profile is None:  # pragma: no cover - resolved by the caller
        raise LookupError("profile not found")
    workspace = Workspace(profile=profile, job=job)
    for requirement in job.requirements:
        if requirement.requirement_type == RequirementType.TECHNOLOGY:
            target = (
                workspace.required_concepts
                if requirement.importance == RequirementImportance.REQUIRED
                else workspace.preferred_concepts
                if requirement.importance == RequirementImportance.PREFERRED
                else None
            )
            if target is not None:
                target.extend(
                    find_technologies(requirement.description) or [requirement.description]
                )

    await index_evidence(session, profile.id, embedder)
    verified = list(
        await session.scalars(
            with_sources(
                select(CandidateEvidence).where(
                    CandidateEvidence.candidate_profile_id == profile.id,
                    CandidateEvidence.verification_status == VerificationStatus.VERIFIED,
                )
            ).options(selectinload(CandidateEvidence.skills))
        )
    )
    # One embedding call: the job, then record titles (for items without evidence).
    records = [
        *((r.id, r.course_name) for r in profile.coursework),
        *((r.id, r.name) for r in profile.certifications),
        *((r.id, r.title) for r in profile.achievements),
        *((r.id, r.title) for r in profile.projects),
    ]
    vectors = await embedder.embed([job_query(job)] + [t for _, t in records], input_type="query")
    job_vector = vectors[0]
    boost = _match_boost(report)
    concepts = set(workspace.required_concepts + workspace.preferred_concepts)

    for evidence in verified:
        similarity = (
            _cosine(job_vector, list(evidence.embedding))
            if evidence.embedding is not None and evidence.embedding_model == embedder.model
            else 0.0
        )
        column = EVIDENCE_SUBJECT_COLUMNS.get(evidence.source_type)
        subject = getattr(evidence, column) if column else None
        item = EvidenceItem(
            id=evidence.id,
            content=evidence.content,
            context=record_label(evidence) or "",
            subject_id=subject,
            relevance=round(similarity + MATCH_BOOST * boost.get(evidence.id, 0.0), 4),
        )
        workspace.evidence[item.id] = item
        if subject is not None:
            workspace.by_subject.setdefault(subject, []).append(item)
        named = set(find_technologies(f"{item.context}\n{item.content}"))
        for skill in [s.normalized_name for s in evidence.skills] + [n.lower() for n in named]:
            workspace.skill_evidence.setdefault(skill, []).append(item)

    for (record_id, title), vector in zip(records, vectors[1:], strict=True):
        own = [e.relevance for e in workspace.by_subject.get(record_id, [])]
        concept_bonus = 0.5 if set(find_technologies(title)) & concepts else 0.0
        workspace.record_relevance[record_id] = max(
            [_cosine(job_vector, vector) + concept_bonus, *own]
        )
    for skill_list in workspace.skill_evidence.values():
        skill_list.sort(key=lambda e: -e.relevance)
    return workspace
