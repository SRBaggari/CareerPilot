"""Evidence indexing and semantic retrieval over pgvector.

- **Chunks:** each evidence row is already one atomic claim. The text embedded for it is
  the claim prefixed with the item it belongs to ("Project: Research Assistant"), so
  retrieval can match on context the claim itself doesn't repeat.
- **Indexing** is lazy and incremental: new, edited (embedding cleared on edit), or
  other-model evidence is embedded before each search. Vectors from different models are
  never compared.
- **Search** is an exact cosine-distance scan over one candidate's evidence. (Combining
  metadata filters with the HNSW index can silently return fewer than ``top_k`` rows,
  because the index is scanned before filtering; per-candidate data is small enough that an
  exact scan is both correct and fast.)
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.embeddings import EmbeddingError, EmbeddingProvider, InputType
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.profiles.models import (
    EVIDENCE_SUBJECT_COLUMNS,
    CandidateEvidence,
    CandidateProfile,
    EvidenceSourceType,
    VerificationStatus,
)
from app.retrieval.schemas import (
    Confidence,
    EvidenceIndexOut,
    EvidenceSearchOut,
    EvidenceSource,
    RetrievedEvidence,
)
from app.users.models import User

EMBED_BATCH = 64
TYPE_LABELS = {
    EvidenceSourceType.PROJECT: "Project",
    EvidenceSourceType.WORK_EXPERIENCE: "Work experience",
    EvidenceSourceType.EDUCATION: "Education",
    EvidenceSourceType.CERTIFICATION: "Certification",
    EvidenceSourceType.ACHIEVEMENT: "Achievement",
    EvidenceSourceType.COURSEWORK: "Coursework",
    EvidenceSourceType.PROFILE: "Profile",
}


# --- Helpers --------------------------------------------------------------------------


def with_sources[Q: Select[Any]](query: Q) -> Q:
    """Eager-load everything ``record_label``/``_source`` read (no lazy loads in async)."""
    return query.options(
        selectinload(CandidateEvidence.project),
        selectinload(CandidateEvidence.work_experience),
        selectinload(CandidateEvidence.education),
        selectinload(CandidateEvidence.certification),
        selectinload(CandidateEvidence.achievement),
        selectinload(CandidateEvidence.coursework),
        selectinload(CandidateEvidence.source_resume),
    )


def record_label(evidence: CandidateEvidence) -> str | None:
    """Human-readable name of the profile item the evidence belongs to."""
    match evidence.source_type:
        case EvidenceSourceType.PROJECT if evidence.project:
            return evidence.project.title
        case EvidenceSourceType.WORK_EXPERIENCE if evidence.work_experience:
            job = evidence.work_experience
            return f"{job.title} at {job.company_name}"
        case EvidenceSourceType.EDUCATION if evidence.education:
            edu = evidence.education
            degree = " in ".join(p for p in (edu.degree, edu.field_of_study) if p)
            return f"{degree}, {edu.institution}" if degree else edu.institution
        case EvidenceSourceType.CERTIFICATION if evidence.certification:
            cert = evidence.certification
            return f"{cert.name} ({cert.issuer})" if cert.issuer else cert.name
        case EvidenceSourceType.ACHIEVEMENT if evidence.achievement:
            return evidence.achievement.title
        case EvidenceSourceType.COURSEWORK if evidence.coursework:
            return evidence.coursework.course_name
    return None


def chunk_text(evidence: CandidateEvidence) -> str:
    """The text embedded for one evidence row: its context plus the verbatim claim."""
    label = record_label(evidence)
    prefix = TYPE_LABELS[evidence.source_type]
    return f"{prefix}: {label}\n{evidence.content}" if label else evidence.content


def confidence_for(similarity: float, thresholds: tuple[float, float]) -> Confidence:
    high, medium = thresholds
    if similarity >= high:
        return "high"
    return "medium" if similarity >= medium else "low"


def evidence_source(evidence: CandidateEvidence) -> EvidenceSource:
    column = EVIDENCE_SUBJECT_COLUMNS.get(evidence.source_type)
    return EvidenceSource(
        record_type=evidence.source_type,
        record_id=getattr(evidence, column) if column else None,
        record_label=record_label(evidence),
        origin=evidence.origin,
        resume_id=evidence.source_resume_id,
        resume_file_name=evidence.source_resume.file_name if evidence.source_resume else None,
    )


async def resolve_candidate(
    session: AsyncSession, user: User, candidate_id: uuid.UUID
) -> CandidateProfile:
    """The candidate profile, only if it belongs to the requesting user (404 otherwise, so
    other candidates' existence is not revealed)."""
    profile = await session.scalar(
        select(CandidateProfile).where(
            CandidateProfile.id == candidate_id, CandidateProfile.user_id == user.id
        )
    )
    if profile is None:
        raise NotFoundError("Candidate not found.")
    return profile


async def _embed(
    provider: EmbeddingProvider, texts: list[str], kind: InputType
) -> list[list[float]]:
    try:
        return await provider.embed(texts, input_type=kind)
    except EmbeddingError as exc:
        raise ServiceUnavailableError(f"Evidence search is unavailable: {exc}") from exc


# --- Indexing -------------------------------------------------------------------------


async def index_evidence(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    provider: EmbeddingProvider,
    *,
    force: bool = False,
) -> EvidenceIndexOut:
    """Embed the candidate's evidence that has no up-to-date embedding for this model."""
    query = select(CandidateEvidence).where(CandidateEvidence.candidate_profile_id == candidate_id)
    total = len((await session.scalars(query)).all())
    if not force:
        query = query.where(
            or_(
                CandidateEvidence.embedding.is_(None),
                CandidateEvidence.embedding_model.is_distinct_from(provider.model),
            )
        )
    pending = list(await session.scalars(with_sources(query.order_by(CandidateEvidence.id))))
    for start in range(0, len(pending), EMBED_BATCH):
        batch = pending[start : start + EMBED_BATCH]
        vectors = await _embed(provider, [chunk_text(e) for e in batch], "document")
        now = datetime.now(UTC)
        for evidence, vector in zip(batch, vectors, strict=True):
            evidence.embedding = vector
            evidence.embedding_model = provider.model
            evidence.embedded_at = now
    if pending:
        await session.commit()
    return EvidenceIndexOut(
        candidate_id=candidate_id,
        embedding_model=provider.model,
        indexed=len(pending),
        total=total,
    )


# --- Retrieval ------------------------------------------------------------------------


async def _nearest(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    query_vector: list[float],
    provider: EmbeddingProvider,
    *,
    top_k: int,
    evidence_types: Sequence[EvidenceSourceType] | None = None,
    include_unverified: bool = False,
    min_similarity: float | None = None,
) -> list[RetrievedEvidence]:
    distance = CandidateEvidence.embedding.cosine_distance(query_vector)
    stmt = (
        select(CandidateEvidence, distance.label("distance"))
        .where(
            CandidateEvidence.candidate_profile_id == candidate_id,
            CandidateEvidence.embedding.is_not(None),
            CandidateEvidence.embedding_model == provider.model,
        )
        # "+ 0" makes the sort key an expression the HNSW index can't serve, forcing an
        # exact scan of this candidate's rows (see module docstring).
        .order_by(distance + literal(0.0), CandidateEvidence.id)
        .limit(top_k)
    )
    if evidence_types:
        stmt = stmt.where(CandidateEvidence.source_type.in_(list(evidence_types)))
    if not include_unverified:
        stmt = stmt.where(CandidateEvidence.verification_status == VerificationStatus.VERIFIED)
    if min_similarity is not None:
        stmt = stmt.where(distance <= 1 - min_similarity)

    results = []
    for evidence, dist in (await session.execute(with_sources(stmt))).all():
        similarity = round(1 - float(dist), 4)
        results.append(
            RetrievedEvidence(
                evidence_id=evidence.id,
                candidate_id=evidence.candidate_profile_id,
                evidence_type=evidence.source_type,
                factual_content=evidence.content,
                similarity=similarity,
                confidence=confidence_for(similarity, provider.confidence_thresholds),
                verification_status=evidence.verification_status,
                source=evidence_source(evidence),
                created_at=evidence.created_at,
                updated_at=evidence.updated_at,
            )
        )
    return results


async def search_evidence(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    query: str,
    provider: EmbeddingProvider,
    *,
    top_k: int = 5,
    evidence_types: Sequence[EvidenceSourceType] | None = None,
    include_unverified: bool = False,
    min_similarity: float | None = None,
) -> EvidenceSearchOut:
    indexed = await index_evidence(session, candidate_id, provider)
    [query_vector] = await _embed(provider, [query], "query")
    results = await _nearest(
        session, candidate_id, query_vector, provider, top_k=top_k,
        evidence_types=evidence_types, include_unverified=include_unverified,
        min_similarity=min_similarity,
    )  # fmt: skip
    return EvidenceSearchOut(
        candidate_id=candidate_id,
        query=query,
        top_k=top_k,
        embedding_model=provider.model,
        retrieval_confidence=results[0].confidence if results else "none",
        newly_indexed=indexed.indexed,
        results=results,
    )


async def retrieve_verified_for_queries(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    queries: Sequence[str],
    provider: EmbeddingProvider,
    *,
    top_k: int = 5,
) -> list[list[RetrievedEvidence]]:
    """Verified evidence for several queries, embedding all queries in one call.

    Like ``retrieve_verified_evidence``, it has no way to return unverified evidence.
    """
    if not queries:
        return []
    await index_evidence(session, candidate_id, provider)
    vectors = await _embed(provider, list(queries), "query")
    return [
        await _nearest(session, candidate_id, vector, provider, top_k=top_k) for vector in vectors
    ]


async def retrieve_verified_evidence(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    query: str,
    provider: EmbeddingProvider,
    *,
    top_k: int = 5,
    evidence_types: Sequence[EvidenceSourceType] | None = None,
    min_similarity: float | None = None,
) -> list[RetrievedEvidence]:
    """The retrieval entry point for application generation.

    Returns verified evidence only; there is deliberately no way to ask it for anything
    else. Results are re-checked here as a second line of defence.
    """
    result = await search_evidence(
        session, candidate_id, query, provider, top_k=top_k, evidence_types=evidence_types,
        include_unverified=False, min_similarity=min_similarity,
    )  # fmt: skip
    verified = [r for r in result.results if r.verification_status == VerificationStatus.VERIFIED]
    if len(verified) != len(result.results):  # pragma: no cover - guarded by the query
        raise RuntimeError("Unverified evidence reached generation retrieval")
    return verified
