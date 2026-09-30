"""Application tracker rules without a database: which moves are allowed, and what an
application needs before the candidate can approve it."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.applications.models import Application, ApplicationStatus
from app.applications.service import allowed_statuses, readiness
from app.documents.models import ApplicationAnswer, CoverLetter, DocumentStatus, TailoredResume

S = ApplicationStatus
NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def application(**fields: Any) -> Application:
    a = Application(id=uuid.uuid4(), status=fields.pop("status", S.SAVED), **fields)
    a.tailored_resume = fields.get("tailored_resume")
    a.cover_letter = fields.get("cover_letter")
    return a


def answer(status: DocumentStatus, text: bool = True) -> ApplicationAnswer:
    sentences = [{"text": "I built data pipelines in Python.", "evidence_ids": []}] if text else []
    return ApplicationAnswer(question="Why us?", status=status, answer={"sentences": sentences})


def resume(status: DocumentStatus = DocumentStatus.VERIFIED) -> TailoredResume:
    return TailoredResume(id=uuid.uuid4(), version=2, status=status)


# --- Allowed moves ------------------------------------------------------------------------------


def test_preparation_stages_move_freely_but_not_to_submitted_without_approval() -> None:
    allowed = allowed_statuses(application(status=S.ANALYZED))
    assert set(allowed) == {
        S.DISCOVERED,
        S.SAVED,
        S.APPLICATION_PREPARED,
        S.AWAITING_APPROVAL,
        S.WITHDRAWN,
    }
    assert S.SUBMITTED not in allowed and S.INTERVIEW not in allowed and S.OFFER not in allowed


def test_approval_unlocks_submission_only() -> None:
    allowed = allowed_statuses(application(status=S.AWAITING_APPROVAL, approved_at=NOW))
    assert S.SUBMITTED in allowed
    assert S.INTERVIEW not in allowed  # later stages need the submission first


def test_after_submission_there_is_no_way_back_to_preparation() -> None:
    a = application(status=S.SUBMITTED, approved_at=NOW, submitted_at=NOW)
    assert set(allowed_statuses(a)) == {S.ASSESSMENT, S.INTERVIEW, S.OFFER, S.REJECTED, S.WITHDRAWN}


@pytest.mark.parametrize(
    ("fields", "reopen_to"),
    [
        ({"status": S.WITHDRAWN}, S.SAVED),  # withdrawn before applying: back to preparation
        ({"status": S.REJECTED, "approved_at": NOW, "submitted_at": NOW}, S.INTERVIEW),
    ],
)
def test_closed_applications_can_be_reopened(fields: dict[str, Any], reopen_to: S) -> None:
    assert reopen_to in allowed_statuses(application(**fields))


# --- Readiness for approval -------------------------------------------------------------------


def test_a_prepared_application_with_verified_documents_and_approved_answers_is_ready() -> None:
    a = application(status=S.APPLICATION_PREPARED, tailored_resume=resume())
    items, blockers = readiness(
        a, [answer(DocumentStatus.APPROVED), answer(DocumentStatus.DRAFT, text=False)]
    )
    assert blockers == []
    assert {i.label: i.ok for i in items} == {
        "Tailored resume": True,
        "Cover letter": True,
        "Application answers": True,
    }


@pytest.mark.parametrize(
    ("fields", "answers", "blocker"),
    [
        ({"status": S.SAVED, "tailored_resume": resume()}, [], "Application prepared"),
        ({"status": S.APPLICATION_PREPARED}, [], "Attach a tailored resume"),
        (
            {
                "status": S.APPLICATION_PREPARED,
                "tailored_resume": resume(DocumentStatus.VERIFICATION_FAILED),
            },
            [],
            "failed verification",
        ),
        (
            {
                "status": S.APPLICATION_PREPARED,
                "tailored_resume": resume(),
                "cover_letter": CoverLetter(version=1, status=DocumentStatus.VERIFICATION_FAILED),
            },
            [],
            "cover letter failed verification",
        ),
        (
            {"status": S.AWAITING_APPROVAL, "tailored_resume": resume()},
            [answer(DocumentStatus.APPROVED), answer(DocumentStatus.VERIFIED)],
            "Approve every application answer (1 of 2 approved)",
        ),
        (
            {"status": S.AWAITING_APPROVAL, "tailored_resume": resume(), "approved_at": NOW},
            [],
            "Already approved",
        ),
    ],
)
def test_what_blocks_approval(
    fields: dict[str, Any], answers: list[ApplicationAnswer], blocker: str
) -> None:
    _, blockers = readiness(application(**fields), answers)
    assert any(blocker in b for b in blockers), blockers
