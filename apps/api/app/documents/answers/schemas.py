import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.documents.cover_letter.content import LetterSentence
from app.documents.cover_letter.schemas import GenerationChanges
from app.documents.models import DocumentStatus, QuestionType
from app.verification.types import VerificationReportOut


class QuestionsIn(BaseModel):
    """Application questions to answer for a job (one answer each)."""

    questions: list[str] = Field(min_length=1, max_length=10)
    max_words: int | None = Field(default=None, ge=20, le=1000)

    @field_validator("questions")
    @classmethod
    def _clean(cls, questions: list[str]) -> list[str]:
        cleaned = [" ".join(q.split()) for q in questions]
        if any(len(q) < 3 or len(q) > 1000 for q in cleaned):
            raise ValueError("Each question must be 3 to 1000 characters.")
        return cleaned


class AnswerEdit(BaseModel):
    """The candidate's edited answer, as text. Every sentence is verified before saving."""

    answer: str = Field(min_length=1, max_length=8000)


class EvidenceUsed(BaseModel):
    evidence_id: uuid.UUID
    content: str
    record_label: str | None


class ApplicationAnswerOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    question: str
    question_type: QuestionType
    focus: str | None
    understanding: str  # how the question was read
    position: int
    max_words: int | None
    status: DocumentStatus
    approved_at: datetime | None
    generator: str | None
    created_at: datetime
    updated_at: datetime
    sentences: list[LetterSentence]
    text: str
    word_count: int
    evidence_used: list[EvidenceUsed]  # every evidence item the answer cites, in order
    changes: GenerationChanges
    notes: list[str]
    report: VerificationReportOut | None  # the latest verification of the answer
