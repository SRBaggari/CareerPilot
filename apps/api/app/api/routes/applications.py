"""The application tracker: dashboard, list with filters and search, detail, lifecycle,
approval, interviews and follow-up reminders.

CareerPilot never submits applications. "Submitted" records that you did, and is only
possible after your explicit approval.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingProvider
from app.ai.provider import LLMProvider
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.verification import get_verification_llm
from app.applications import review, service
from app.applications.approval import ReviewPackage
from app.applications.models import ApplicationStatus
from app.applications.review import ApprovalIn, RejectionIn
from app.applications.schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationSummaryOut,
    ApplicationUpdate,
    DashboardOut,
    FollowUpIn,
    FollowUpUpdate,
    InterviewIn,
    InterviewUpdate,
    StatusChange,
)
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])
Session = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=list[ApplicationSummaryOut])
async def list_applications(
    session: Session,
    user: CurrentUser,
    status_: Annotated[list[ApplicationStatus] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    follow_up_due: bool = False,
    sort: Literal["updated", "company", "discovered", "applied"] = "updated",
) -> list[ApplicationSummaryOut]:
    """Filter by status (repeatable), search company, position, location and notes."""
    return await service.list_applications(
        session, user, statuses=status_, q=q, follow_up_due=follow_up_due, sort=sort
    )


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(session: Session, user: CurrentUser) -> DashboardOut:
    """Counts by status, follow-ups due, upcoming interviews, recent applications."""
    return await service.dashboard(session, user)


@router.post("", response_model=ApplicationOut)
async def create_application(
    payload: ApplicationCreate, response: Response, session: Session, user: CurrentUser
) -> ApplicationOut:
    """Start tracking an application for one of your jobs (once per job: 201, or 200 with
    the existing one). The latest tailored resume and cover letter are attached."""
    application, created = await service.create(session, user, payload)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return application


@router.get("/{application_id}", response_model=ApplicationOut)
async def get_application(
    application_id: uuid.UUID, session: Session, user: CurrentUser
) -> ApplicationOut:
    return await service.get(session, user, application_id)


@router.patch("/{application_id}", response_model=ApplicationOut)
async def update_application(
    application_id: uuid.UUID, payload: ApplicationUpdate, session: Session, user: CurrentUser
) -> ApplicationOut:
    """Notes, the job URL, and which resume and cover letter the application uses."""
    return await service.update(session, user, application_id, payload)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_application(
    application_id: uuid.UUID, session: Session, user: CurrentUser
) -> None:
    await service.delete(session, user, application_id)


@router.post("/{application_id}/status", response_model=ApplicationOut)
async def change_status(
    application_id: uuid.UUID, payload: StatusChange, session: Session, user: CurrentUser
) -> ApplicationOut:
    """Move the application. Submission requires your approval first (409 otherwise)."""
    return await service.change_status(session, user, application_id, payload)


@router.get("/{application_id}/review", response_model=ReviewPackage)
async def get_review(
    application_id: uuid.UUID, session: Session, user: CurrentUser
) -> ReviewPackage:
    """Everything to check before submitting: job, company, resume, cover letter, answers,
    personal information, verification results, and missing or uncertain fields. Opening
    it is recorded and never approves anything."""
    return await review.get_review(session, user, application_id)


@router.post("/{application_id}/review/request", response_model=ReviewPackage)
async def request_review(
    application_id: uuid.UUID, session: Session, user: CurrentUser
) -> ReviewPackage:
    """Mark the application ready for your review (from draft or rejected)."""
    return await review.request_review(session, user, application_id)


@router.post("/{application_id}/approve", response_model=ReviewPackage)
async def approve_application(
    application_id: uuid.UUID,
    payload: ApprovalIn,
    session: Session,
    user: CurrentUser,
    settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder),
    llm: LLMProvider | None = Depends(get_verification_llm),
) -> ReviewPackage:
    """Your explicit approval of the exact content you reviewed (``content_hash`` from the
    review, ``confirm: true``). The resume and cover letter are verified again against
    your current evidence first. Refused if anything is missing or unverified. It submits
    nothing."""
    return await review.approve(session, user, application_id, payload, embedder, llm, settings)


@router.post("/{application_id}/reject", response_model=ReviewPackage)
async def reject_application(
    application_id: uuid.UUID, payload: RejectionIn, session: Session, user: CurrentUser
) -> ReviewPackage:
    """Reject the application, or withdraw your approval."""
    return await review.reject(session, user, application_id, payload)


@router.post("/{application_id}/interviews", response_model=ApplicationOut)
async def add_interview(
    application_id: uuid.UUID, payload: InterviewIn, session: Session, user: CurrentUser
) -> ApplicationOut:
    return await service.add_interview(session, user, application_id, payload)


@router.patch("/{application_id}/interviews/{interview_id}", response_model=ApplicationOut)
async def update_interview(
    application_id: uuid.UUID,
    interview_id: uuid.UUID,
    payload: InterviewUpdate,
    session: Session,
    user: CurrentUser,
) -> ApplicationOut:
    return await service.update_interview(session, user, application_id, interview_id, payload)


@router.delete("/{application_id}/interviews/{interview_id}", response_model=ApplicationOut)
async def delete_interview(
    application_id: uuid.UUID, interview_id: uuid.UUID, session: Session, user: CurrentUser
) -> ApplicationOut:
    return await service.delete_interview(session, user, application_id, interview_id)


@router.post("/{application_id}/follow-ups", response_model=ApplicationOut)
async def add_follow_up(
    application_id: uuid.UUID, payload: FollowUpIn, session: Session, user: CurrentUser
) -> ApplicationOut:
    return await service.add_follow_up(session, user, application_id, payload)


@router.patch("/{application_id}/follow-ups/{follow_up_id}", response_model=ApplicationOut)
async def update_follow_up(
    application_id: uuid.UUID,
    follow_up_id: uuid.UUID,
    payload: FollowUpUpdate,
    session: Session,
    user: CurrentUser,
) -> ApplicationOut:
    return await service.update_follow_up(session, user, application_id, follow_up_id, payload)


@router.delete("/{application_id}/follow-ups/{follow_up_id}", response_model=ApplicationOut)
async def delete_follow_up(
    application_id: uuid.UUID, follow_up_id: uuid.UUID, session: Session, user: CurrentUser
) -> ApplicationOut:
    return await service.delete_follow_up(session, user, application_id, follow_up_id)
