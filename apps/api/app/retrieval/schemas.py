import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.profiles.models import EvidenceOrigin, EvidenceSourceType, VerificationStatus
from app.profiles.schemas import InputModel

Confidence = Literal["high", "medium", "low"]


class EvidenceSearchIn(InputModel):
    candidate_id: uuid.UUID
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=50)
    evidence_type: EvidenceSourceType | None = None
    # Unverified evidence is excluded unless explicitly requested (e.g. to show a reviewer).
    # It must never be used to generate application material.
    include_unverified: bool = False
    min_similarity: float | None = Field(default=None, ge=-1, le=1)


class EvidenceSource(BaseModel):
    """Where a piece of evidence came from, so every retrieved claim is traceable."""

    record_type: EvidenceSourceType
    record_id: uuid.UUID | None  # the project / job / degree ... it belongs to
    record_label: str | None  # e.g. "Machine Learning Intern at Acme"
    origin: EvidenceOrigin
    resume_id: uuid.UUID | None
    resume_file_name: str | None


class RetrievedEvidence(BaseModel):
    evidence_id: uuid.UUID
    candidate_id: uuid.UUID
    evidence_type: EvidenceSourceType
    factual_content: str
    similarity: float  # cosine similarity between query and evidence embeddings
    confidence: Confidence
    verification_status: VerificationStatus
    source: EvidenceSource
    created_at: datetime
    updated_at: datetime


class EvidenceSearchOut(BaseModel):
    candidate_id: uuid.UUID
    query: str
    top_k: int
    embedding_model: str
    retrieval_confidence: Confidence | Literal["none"]  # confidence of the best match
    newly_indexed: int  # evidence embedded on demand before this search
    results: list[RetrievedEvidence]


class EvidenceIndexIn(InputModel):
    candidate_id: uuid.UUID
    force: bool = False  # re-embed everything, not only new/changed evidence


class EvidenceIndexOut(BaseModel):
    candidate_id: uuid.UUID
    embedding_model: str
    indexed: int
    total: int
