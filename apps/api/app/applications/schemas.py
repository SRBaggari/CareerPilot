import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.applications.models import (
    PRE_SUBMISSION,
    ApplicationStatus,
    ApprovalState,
    FollowUpChannel,
    FollowUpStatus,
    InterviewStatus,
    InterviewType,
)
from app.documents.models import DocumentStatus


def _http(value: str | None) -> str | None:
    if value is not None and value and not value.startswith(("https://", "http://")):
        raise ValueError("Must be an http(s) URL.")
    return value or None


# --- Input ------------------------------------------------------------------------------------


class ApplicationCreate(BaseModel):
    job_id: uuid.UUID
    status: ApplicationStatus = ApplicationStatus.SAVED
    notes: str | None = Field(default=None, max_length=10000)

    @field_validator("status")
    @classmethod
    def _not_yet_submitted(cls, value: ApplicationStatus) -> ApplicationStatus:
        if value not in PRE_SUBMISSION:
            raise ValueError("A new application starts before submission.")
        return value


class ApplicationUpdate(BaseModel):
    """Only the fields sent are changed. Send ``null`` to detach a document."""

    notes: str | None = Field(default=None, max_length=10000)
    application_url: str | None = Field(default=None, max_length=2000)
    tailored_resume_id: uuid.UUID | None = None
    cover_letter_id: uuid.UUID | None = None

    @field_validator("application_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return _http(value)


class StatusChange(BaseModel):
    status: ApplicationStatus
    note: str | None = Field(default=None, max_length=2000)
    submitted_on: date | None = None  # the date you applied, when marking it submitted


class InterviewIn(BaseModel):
    interview_type: InterviewType
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    location: str | None = Field(default=None, max_length=300)
    meeting_url: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("meeting_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return _http(value)


class InterviewUpdate(BaseModel):
    status: InterviewStatus | None = None
    scheduled_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=5000)


class FollowUpIn(BaseModel):
    due_at: datetime
    channel: FollowUpChannel = FollowUpChannel.EMAIL
    subject: str = Field(min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)
    interview_id: uuid.UUID | None = None


class FollowUpUpdate(BaseModel):
    status: FollowUpStatus | None = None
    due_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=5000)


# --- Output -----------------------------------------------------------------------------------


class DocumentRef(BaseModel):
    id: uuid.UUID
    version: int
    status: DocumentStatus
    created_at: datetime
    newer_version: int | None  # a newer version exists for this job


class AnswerRef(BaseModel):
    id: uuid.UUID
    question: str
    status: DocumentStatus
    approved: bool


class ReadinessItem(BaseModel):
    label: str
    ok: bool
    detail: str
    required: bool  # needed before approval


class InterviewOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    company: str
    position: str
    interview_type: InterviewType
    status: InterviewStatus
    scheduled_at: datetime | None
    duration_minutes: int | None
    location: str | None
    meeting_url: str | None
    notes: str | None


class FollowUpOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    company: str
    position: str
    interview_id: uuid.UUID | None
    channel: FollowUpChannel
    status: FollowUpStatus
    due_at: datetime | None
    completed_at: datetime | None
    subject: str | None
    notes: str | None
    overdue: bool


class TimelineEvent(BaseModel):
    at: datetime
    kind: Literal["created", "status", "approval", "interview", "follow_up", "document", "answer"]
    title: str
    detail: str | None = None
    upcoming: bool = False  # in the future (a scheduled interview, a follow-up due)


class ApplicationSummaryOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    company: str
    position: str
    location: str | None
    job_url: str | None
    status: ApplicationStatus
    discovered_at: datetime | None
    applied_at: datetime | None  # when you submitted it
    approved_at: datetime | None
    approval_state: ApprovalState
    updated_at: datetime
    next_interview_at: datetime | None
    next_follow_up_at: datetime | None
    overdue_follow_ups: int
    has_resume: bool
    has_cover_letter: bool
    answers_approved: int
    answers_total: int


class ApplicationOut(ApplicationSummaryOut):
    notes: str | None
    resume: DocumentRef | None
    cover_letter: DocumentRef | None
    answers: list[AnswerRef]
    readiness: list[ReadinessItem]
    approval_blockers: list[str]  # why it can't be approved yet (empty when it can)
    allowed_statuses: list[ApplicationStatus]  # where it can move next
    interviews: list[InterviewOut]
    follow_ups: list[FollowUpOut]
    timeline: list[TimelineEvent]


class DashboardOut(BaseModel):
    counts: dict[ApplicationStatus, int]
    total: int
    active: int  # not rejected or withdrawn
    follow_ups_due: list[FollowUpOut]  # overdue or due within 7 days
    upcoming_interviews: list[InterviewOut]  # within 14 days
    recent: list[ApplicationSummaryOut]  # most recently updated
