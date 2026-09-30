"""Audit log of every AI provider call (LLM or embedding)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, enum_column, fk_column


class AIOperation(StrEnum):
    RESUME_PARSING = "resume_parsing"
    EVIDENCE_EXTRACTION = "evidence_extraction"
    JOB_ANALYSIS = "job_analysis"
    EMBEDDING = "embedding"
    MATCHING = "matching"
    SKILL_GAP_ANALYSIS = "skill_gap_analysis"
    RESUME_GENERATION = "resume_generation"
    COVER_LETTER_GENERATION = "cover_letter_generation"
    APPLICATION_ANSWER = "application_answer"
    CLAIM_VERIFICATION = "claim_verification"
    OTHER = "other"


class AIExecutionStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


class AIExecutionLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only. Payload columns may contain personal data: store them only when needed
    for debugging, and purge them under the retention policy."""

    __tablename__ = "ai_execution_logs"
    __table_args__ = (
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0 "
            "AND estimated_cost_usd >= 0",
            "non_negative_usage",
        ),
        CheckConstraint("status <> 'error' OR error_message IS NOT NULL", "error_has_message"),
        Index("ix_ai_execution_logs_user_created", "user_id", "created_at"),
        Index("ix_ai_execution_logs_operation_created", "operation", "created_at"),
    )

    # SET NULL keeps usage/cost history if a user is deleted (payloads should be purged).
    user_id: Mapped[uuid.UUID | None] = fk_column(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    operation: Mapped[AIOperation] = enum_column(AIOperation)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[AIExecutionStatus] = enum_column(AIExecutionStatus)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    error_message: Mapped[str | None] = mapped_column(Text)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
