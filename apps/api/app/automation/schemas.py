import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.automation.models import AuditActor, RunStatus
from app.automation.plan import Problem
from app.automation.review import Review


class RunStart(BaseModel):
    # Values for fields CareerPilot can't fill from your records, by field id.
    inputs: dict[str, str] = Field(default_factory=dict, max_length=50)


class RunInputs(BaseModel):
    inputs: dict[str, str] = Field(max_length=50)


class RunConfirm(BaseModel):
    """Your explicit confirmation of one specific review."""

    review_hash: str = Field(min_length=64, max_length=64)
    confirm: bool


class AuditEventOut(BaseModel):
    id: uuid.UUID
    at: datetime
    actor: AuditActor
    action: str
    message: str
    detail: dict[str, Any]


class RunOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    status: RunStatus
    destination_url: str
    destination_host: str
    adapter: str | None
    inputs: dict[str, str]
    review: Review | None
    review_hash: str | None
    problems: list[Problem]
    stop_reason: str | None
    prepared_at: datetime | None
    confirmed_at: datetime | None
    submitted_at: datetime | None
    confirmation_reference: str | None
    nothing_submitted: bool  # true until the site confirmed a submission
    created_at: datetime
    updated_at: datetime
    events: list[AuditEventOut]
