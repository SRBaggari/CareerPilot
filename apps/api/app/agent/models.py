"""Agent runs and the agent execution log."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.agent.machine import Stage
from app.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    fk_column,
)


class RunStatus(StrEnum):
    READY = "ready"  # can advance
    RUNNING = "running"  # a request is advancing it; others are refused
    WAITING_FOR_HUMAN = "waiting_for_human"  # paused: see ``pause``
    COMPLETED = "completed"
    FAILED = "failed"  # an unexpected error; advancing retries the stage
    CANCELLED = "cancelled"


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"  # transitions and pauses
    CANDIDATE_PROFILE = "candidate_profile"
    JOB_DISCOVERY = "job_discovery"
    JOB_ANALYSIS = "job_analysis"
    MATCHING = "matching"
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    CLAIM_VERIFICATION = "claim_verification"
    APPLICATION_PREPARATION = "application_preparation"
    HUMAN_APPROVAL = "human_approval"
    APPLICATION_TRACKING = "application_tracking"


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"  # nothing to do (e.g. already done)
    PAUSED = "paused"  # stopped for human input
    FAILED = "failed"


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "(status = 'waiting_for_human') = (pause IS NOT NULL)", "paused_has_reason"
        ),
        CheckConstraint("(stage = 'done') = (status = 'completed')", "done_is_completed"),
        CheckConstraint("steps >= 0", "steps_non_negative"),
    )

    candidate_profile_id: Mapped[uuid.UUID] = fk_column("candidate_profiles.id")
    job_id: Mapped[uuid.UUID | None] = fk_column("jobs.id", nullable=True)
    application_id: Mapped[uuid.UUID | None] = fk_column("applications.id", nullable=True)
    stage: Mapped[Stage] = enum_column(Stage, default=Stage.DISCOVER, server_default="discover")
    status: Mapped[RunStatus] = enum_column(
        RunStatus, default=RunStatus.READY, server_default="ready"
    )
    # What the candidate asked for: a job (or a posting to import), options, questions.
    goal: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Decisions only the candidate can make (e.g. confirming uncertain eligibility).
    inputs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # {kind, message, items}; SQL NULL (not JSON null) when not paused.
    pause: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    steps: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # tool calls
    last_error: Mapped[str | None] = mapped_column(Text)


class AgentActionLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only execution log: one row per tool call, transition or pause.

    Summaries and errors are redacted before they are stored: no API keys or other
    secrets are ever written here.
    """

    __tablename__ = "agent_action_logs"
    __table_args__ = (
        Index("ix_agent_action_logs_run_created", "run_id", "created_at"),
        CheckConstraint("duration_ms >= 0", "duration_non_negative"),
    )

    run_id: Mapped[uuid.UUID] = fk_column("agent_runs.id", index=False)
    agent: Mapped[AgentName] = enum_column(AgentName)
    stage: Mapped[Stage] = enum_column(Stage)
    task: Mapped[str] = mapped_column(String(300))
    tool: Mapped[str] = mapped_column(String(100))
    input_summary: Mapped[str] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ActionStatus] = enum_column(ActionStatus)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
