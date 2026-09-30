import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.jobs.models import RequirementImportance, RequirementType
from app.matching.models import MatchStatus
from app.retrieval.schemas import EvidenceSource


class MatchingEvidence(BaseModel):
    evidence_id: uuid.UUID
    factual_content: str
    similarity: float | None
    source: EvidenceSource


class RequirementMatchOut(BaseModel):
    requirement_id: uuid.UUID
    requirement: str
    requirement_type: RequirementType
    importance: RequirementImportance
    matching_candidate_evidence: list[MatchingEvidence]
    semantic_similarity: float | None
    match_status: MatchStatus
    explanation: str
    evidence_ids: list[uuid.UUID]
    judge: str
    details: dict[str, Any]


class ScoresOut(BaseModel):
    evidence_coverage: float  # weighted share of assessable requirements covered
    required_coverage: float | None
    preferred_coverage: float | None
    semantic_similarity: float | None  # mean best similarity across requirements
    skill_coverage: float | None


class MatchReportOut(BaseModel):
    job_id: uuid.UUID
    job_title: str
    company_name: str
    candidate_id: uuid.UUID
    computed_at: datetime
    matcher: str | None
    embedding_model: str | None
    scoring_version: str
    disclaimer: str
    summary: str | None
    scores: ScoresOut
    status_counts: dict[MatchStatus, int]
    requirements: list[RequirementMatchOut]
    strongest_matches: list[RequirementMatchOut]
    missing_skills: list[RequirementMatchOut]
    partial_matches: list[RequirementMatchOut]
    potentially_disqualifying: list[RequirementMatchOut]
    informational_not_scored: int
    warnings: list[str]
    is_stale: bool  # the profile changed since this match was computed
