"""Human-in-the-loop approval: the review package, explicit content-bound approval, the
submission gate, the audit trail, and security and authorization."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.models import Application, ApplicationApproval
from app.documents.models import ClaimStatus, GeneratedClaim
from app.users.dependencies import get_current_user
from app.users.models import User

from ..settings_helpers import production_settings
from .conftest import client_for, make_user
from .test_applications_api import APPS, _client, approve, move, ok, prepared
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, post

pytestmark = pytest.mark.anyio
PROFILE = "/api/v1/profile"


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def ready(api: httpx2.AsyncClient, *, letter: bool = True) -> dict[str, Any]:
    """A prepared application with a cover letter and an approved answer, ready for review."""
    app, _ = await prepared(api)
    if letter:
        letter_doc = await post(api, f"{JOBS}/{app['job_id']}/cover-letters")
        await ok(await api.patch(f"{APPS}/{app['id']}", json={"cover_letter_id": letter_doc["id"]}))
    answers = await post(
        api,
        f"{JOBS}/{app['job_id']}/application-answers",
        {"questions": ["Describe your experience with Python."]},
    )
    for answer in answers if isinstance(answers, list) else answers["answers"]:
        await post(api, f"/api/v1/application-answers/{answer['id']}/approve")
    await ok(await api.post(f"{APPS}/{app['id']}/review/request"))
    return app


async def review(api: httpx2.AsyncClient, app_id: str) -> dict[str, Any]:
    return await ok(await api.get(f"{APPS}/{app_id}/review"))


def actions(package: dict[str, Any]) -> list[str]:
    return [e["action"] for e in reversed(package["events"])]  # oldest first


async def approve_with(api: httpx2.AsyncClient, app_id: str, **body: Any) -> httpx2.Response:
    return await api.post(f"{APPS}/{app_id}/approve", json=body)


# --- The review package ---------------------------------------------------------------------


async def test_the_review_shows_everything_and_opening_it_approves_nothing(
    api: httpx2.AsyncClient,
) -> None:
    app = await ready(api)
    for _ in range(3):
        package = await review(api, app["id"])
    assert package["approval_state"] == "ready_for_review" and package["approval"] is None
    assert actions(package).count("review_opened") == 3
    assert not package["can_submit"] and "haven't approved" in package["submit_blockers"][0]

    # 1-2. Job and company.
    assert (package["job"]["title"], package["job"]["company"]) == ("ML Engineer", "Northwind")
    # 3-4. Resume and cover letter, with their content.
    assert package["resume"]["content"]["header"]["full_name"] == "Test Candidate"
    assert package["cover_letter"]["content"]["company_name"] == "Northwind"
    # 5. Application answers.
    [answer] = package["answers"]
    assert answer["question"] == "Describe your experience with Python." and answer["approved"]
    # 6. Personal information.
    personal = package["personal"]
    assert personal["full_name"] == "Test Candidate" and personal["email"] == "t@example.test"
    # 7. Evidence verification results for every document.
    for verification in (
        package["resume"]["verification"],
        package["cover_letter"]["verification"],
        answer["verification"],
    ):
        assert verification["verified"] and verification["outcome"] == "approved"
        assert verification["claims_verified"] > 0 and verification["unverified"] == []
        assert verification["counts"]["supported"] > 0 and verification["checked_at"]
    # 8. Missing or uncertain fields.
    warnings = [i["message"] for i in package["issues"] if i["severity"] == "warning"]
    assert "No phone number in your profile." in warnings
    assert not [i for i in package["issues"] if i["severity"] == "blocker"]
    assert package["can_approve"] and len(package["content_hash"]) == 64

    tracked = await ok(await api.get(f"{APPS}/{app['id']}"))
    assert tracked["approval_state"] == "ready_for_review" and tracked["approved_at"] is None


async def test_states_move_from_draft_through_review(api: httpx2.AsyncClient) -> None:
    app, _ = await prepared(api)
    assert (await ok(await api.get(f"{APPS}/{app['id']}")))["approval_state"] == "draft"
    early = await approve_with(
        api, app["id"], content_hash=(await review(api, app["id"]))["content_hash"], confirm=True
    )
    assert early.status_code == 409 and "ready for review" in early.text  # not from draft
    package = await ok(await api.post(f"{APPS}/{app['id']}/review/request"))
    assert package["approval_state"] == "ready_for_review"
    assert (await api.post(f"{APPS}/{app['id']}/review/request")).status_code == 409


# --- Explicit, content-bound approval -------------------------------------------------------


async def test_approval_is_explicit_and_bound_to_the_reviewed_content(
    api: httpx2.AsyncClient, db: AsyncSession, user: User
) -> None:
    app = await ready(api)
    package = await review(api, app["id"])
    shown = package["content_hash"]

    assert (await approve_with(api, app["id"])).status_code == 422  # nothing confirmed
    assert (await approve_with(api, app["id"], content_hash=shown)).status_code == 422
    refused = await approve_with(api, app["id"], content_hash=shown, confirm=False)
    assert refused.status_code == 422 and "Confirm" in refused.text
    stale = await approve_with(api, app["id"], content_hash="0" * 64, confirm=True)
    assert stale.status_code == 409 and "changed since you opened the review" in stale.text
    # The reviewer is whoever is signed in; it can't be supplied.
    spoofed = await approve_with(
        api, app["id"], content_hash=shown, confirm=True, reviewer_id=str(uuid.uuid4())
    )
    assert spoofed.status_code == 422
    assert (await review(api, app["id"]))["approval_state"] == "ready_for_review"

    approved = await ok(await approve_with(api, app["id"], content_hash=shown, confirm=True))
    assert approved["approval_state"] == "approved" and approved["can_submit"]
    current = approved["approval"]
    assert current["reviewer"] == user.email and current["content_hash"] == shown
    assert current["version"] == 1 and current["approved_at"]
    [record] = approved["history"]
    assert record["decision"] == "approved" and record["reviewer"] == user.email

    row = await db.scalar(select(Application).where(Application.id == uuid.UUID(app["id"])))
    assert row is not None and row.approved_by_id == user.id
    assert row.approved_content_hash == shown
    stored = await db.scalar(select(ApplicationApproval))
    assert stored is not None and stored.content_hash == shown
    assert stored.content["job"]["company"] == "Northwind"  # the reviewed content is kept
    assert stored.content["answers"][0]["question"] == "Describe your experience with Python."
    assert (
        actions(approved).index("review_requested")
        < actions(approved).index("review_opened")
        < actions(approved).index("approved")
    )
    [event] = [e for e in approved["events"] if e["action"] == "approved"]
    assert event["user"] == user.email and event["actor"] == "user"


async def test_missing_and_unverified_content_blocks_approval(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    app = await ready(api, letter=False)
    await db.execute(
        update(GeneratedClaim)
        .where(GeneratedClaim.tailored_resume_id == uuid.UUID(app["resume"]["id"]))
        .where(GeneratedClaim.status == ClaimStatus.VERIFIED)
        .values(status=ClaimStatus.UNSUPPORTED)
    )
    package = await review(api, app["id"])
    resume = package["resume"]["verification"]
    assert not resume["verified"] and resume["unverified"]
    assert any(i["section"] == "resume" and i["severity"] == "blocker" for i in package["issues"])
    assert not package["can_approve"]
    # Approval verifies the resume again, against the profile as it is now: a stale "not
    # verified" is corrected, and content the profile no longer backs is refused.
    await ok(await api.patch(PROFILE, json={"full_name": "Someone Renamed"}))
    package = await review(api, app["id"])
    blocked = await approve_with(api, app["id"], content_hash=package["content_hash"], confirm=True)
    assert blocked.status_code == 409 and "aren't verified" in blocked.text
    assert "approval_blocked" in actions(await review(api, app["id"]))

    # Without a resume at all: a required field is missing.
    await ok(await api.patch(f"{APPS}/{app['id']}", json={"tailored_resume_id": None}))
    package = await review(api, app["id"])
    assert "Attach a tailored resume." in [i["message"] for i in package["issues"]]
    missing = await approve_with(api, app["id"], content_hash=package["content_hash"], confirm=True)
    assert missing.status_code == 409 and "Attach a tailored resume" in missing.text


# --- The submission gate --------------------------------------------------------------------


async def test_submission_needs_the_candidates_approval(api: httpx2.AsyncClient) -> None:
    app = await ready(api)
    refused = await move(api, app["id"], "submitted")
    assert refused.status_code == 409 and "haven't approved" in refused.text
    package = await review(api, app["id"])
    assert "submission_blocked" in actions(package)
    assert package["approval_state"] == "ready_for_review" and package["submitted_at"] is None


async def test_changes_after_approval_withdraw_it_and_block_submission(
    api: httpx2.AsyncClient,
) -> None:
    app = await ready(api)
    await ok(await approve(api, app["id"]))
    await ok(await api.patch(PROFILE, json={"phone": "+91 90000 00000"}))

    refused = await move(api, app["id"], "submitted")
    assert refused.status_code == 409
    assert "changed after you approved it (your personal information)" in refused.text
    package = await review(api, app["id"])
    assert package["approval_state"] == "ready_for_review" and package["approval"] is None
    log = actions(package)
    assert log.index("approval_invalidated") < log.index("submission_blocked")
    [invalidated] = [e for e in package["events"] if e["action"] == "approval_invalidated"]
    assert invalidated["actor"] == "system" and invalidated["detail"]["changed"] == ["personal"]
    assert package["personal"]["phone"] == "+91 90000 00000"  # the review shows the change

    # The resume and letter still carry the old contact details, which approval's fresh
    # verification catches; regenerated, the new version can be approved.
    stale = await approve_with(api, app["id"], content_hash=package["content_hash"], confirm=True)
    assert stale.status_code == 409 and "aren't verified" in stale.text
    resume = await post(api, f"{JOBS}/{app['job_id']}/tailored-resumes")
    letter = await post(api, f"{JOBS}/{app['job_id']}/cover-letters")
    attach = {"tailored_resume_id": resume["id"], "cover_letter_id": letter["id"]}
    await ok(await api.patch(f"{APPS}/{app['id']}", json=attach))
    package = await review(api, app["id"])

    # Approving the new version allows submission; the history keeps both versions.
    approved = await ok(
        await approve_with(api, app["id"], content_hash=package["content_hash"], confirm=True)
    )
    assert approved["approval"]["version"] == 2
    assert [h["version"] for h in approved["history"]] == [2, 1]
    submitted = await ok(await move(api, app["id"], "submitted"))
    assert submitted["status"] == "submitted" and submitted["approval_state"] == "submitted"
    final = await review(api, app["id"])
    assert final["submitted_at"] and actions(final)[-1] in ("submitted", "review_opened")
    assert "submitted" in actions(final)
    assert final["submit_blockers"] == ["Already submitted."]
    assert (await api.post(f"{APPS}/{app['id']}/reject", json={})).status_code == 409


async def test_changing_where_to_apply_withdraws_the_approval_at_once(
    api: httpx2.AsyncClient,
) -> None:
    app = await ready(api)
    await ok(await approve(api, app["id"]))
    changed = await ok(
        await api.patch(f"{APPS}/{app['id']}", json={"application_url": "https://evil.example/x"})
    )
    assert changed["approval_state"] == "ready_for_review" and changed["approved_at"] is None
    assert (await move(api, app["id"], "submitted")).status_code == 409


async def test_claims_unverified_after_approval_block_submission(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    app = await ready(api, letter=False)
    await ok(await approve(api, app["id"]))
    await db.execute(
        update(GeneratedClaim)
        .where(GeneratedClaim.tailored_resume_id == uuid.UUID(app["resume"]["id"]))
        .values(status=ClaimStatus.PENDING)
    )
    refused = await move(api, app["id"], "submitted")
    assert refused.status_code == 409 and "aren't verified" in refused.text
    assert (await ok(await api.get(f"{APPS}/{app['id']}")))["applied_at"] is None


async def test_rejecting_and_withdrawing_an_approval(api: httpx2.AsyncClient) -> None:
    app = await ready(api)
    rejected = await ok(
        await api.post(f"{APPS}/{app['id']}/reject", json={"note": "Wrong salary range."})
    )
    assert rejected["approval_state"] == "rejected"
    assert rejected["history"][0]["decision"] == "rejected"
    assert rejected["history"][0]["note"] == "Wrong salary range."
    assert (await move(api, app["id"], "submitted")).status_code == 409

    await ok(await approve(api, app["id"]))  # back to review, then approved
    withdrawn = await ok(await api.post(f"{APPS}/{app['id']}/reject", json={}))
    assert withdrawn["approval_state"] == "rejected" and withdrawn["approval"] is None
    assert [h["decision"] for h in withdrawn["history"]] == ["rejected", "approved", "rejected"]
    assert (await move(api, app["id"], "submitted")).status_code == 409


# --- Security and authorization -------------------------------------------------------------


async def test_other_users_cannot_see_or_decide(
    api: httpx2.AsyncClient, db: AsyncSession, embedder: SpyEmbedder
) -> None:
    app = await ready(api)
    shown = (await review(api, app["id"]))["content_hash"]
    stranger = await make_user(db, "stranger@example.test")
    async with _client(db, stranger, embedder) as other:
        await post(other, PROFILE, {"full_name": "Someone Else"})
        for method, path, body in (
            ("GET", f"{APPS}/{app['id']}/review", None),
            ("POST", f"{APPS}/{app['id']}/review/request", None),
            ("POST", f"{APPS}/{app['id']}/approve", {"content_hash": shown, "confirm": True}),
            ("POST", f"{APPS}/{app['id']}/reject", {"note": "x"}),
            ("POST", f"{APPS}/{app['id']}/status", {"status": "submitted"}),
        ):
            response = await other.request(method, path, json=body)
            assert response.status_code == 404, (method, path)
    package = await review(api, app["id"])
    assert package["approval_state"] == "ready_for_review" and package["history"] == []
    assert all(e["user"] != "stranger@example.test" for e in package["events"])


async def test_requests_without_an_identity_are_refused(db: AsyncSession, user: User) -> None:
    production = production_settings()  # proxy auth, but no proxy headers sent
    async with client_for(
        db, user, settings=production, overrides={get_current_user: get_current_user}
    ) as anonymous:
        app_id = uuid.uuid4()
        for method, path, body in (
            ("GET", f"{APPS}/{app_id}/review", None),
            ("POST", f"{APPS}/{app_id}/review/request", None),
            ("POST", f"{APPS}/{app_id}/approve", {"content_hash": "0" * 64, "confirm": True}),
            ("POST", f"{APPS}/{app_id}/reject", {}),
            ("POST", f"{APPS}/{app_id}/status", {"status": "submitted"}),
        ):
            response = await anonymous.request(method, path, json=body)
            assert response.status_code == 401, (method, path)


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ("approval_state = 'approved', approved_at = now()", "approval_recorded"),
        ("approved_at = now()", "unapproved_has_no_approval"),
        ("approval_state = 'submitted'", "submitted_state_matches"),
    ],
)
async def test_the_database_refuses_inconsistent_approvals(
    api: httpx2.AsyncClient, db: AsyncSession, change: str, constraint: str
) -> None:
    app = await ready(api, letter=False)
    with pytest.raises(IntegrityError, match=constraint):
        await db.execute(
            text(f"UPDATE applications SET {change} WHERE id = :id"),  # noqa: S608 - fixed SQL
            {"id": app["id"]},
        )
