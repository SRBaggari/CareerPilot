"""The application tracker end to end: tracking a job, the lifecycle and its approval gate,
interviews and follow-up reminders, the timeline, the dashboard, filters and search."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.matching import get_match_llm
from app.core.config import Settings
from app.users.models import User

from .conftest import client_for, make_user
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, PROFILE, build_job, post
from .test_tailored_resume_api import build_profile

pytestmark = pytest.mark.anyio
APPS = "/api/v1/applications"


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


def _client(db: AsyncSession, user: User, embedder: SpyEmbedder) -> httpx2.AsyncClient:
    overrides: dict[Any, Any] = {get_embedder: lambda: embedder, get_match_llm: lambda: None}
    return client_for(
        db, user, settings=Settings(_env_file=None, app_env="test"), overrides=overrides
    )


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def make_job(api: httpx2.AsyncClient, title: str, company: str, location: str) -> str:
    job = await post(
        api,
        JOBS,
        {
            "title": title,
            "company_name": company,
            "location": location,
            "source_url": f"https://jobs.example.com/{company.lower().replace(' ', '-')}",
            "requirements": [
                {
                    "requirement_type": "technology",
                    "importance": "required",
                    "description": "Python",
                }
            ],
        },
    )
    return job["id"]  # type: ignore[no-any-return]


async def track(api: httpx2.AsyncClient, job_id: str, **fields: Any) -> dict[str, Any]:
    response = await api.post(APPS, json={"job_id": job_id, **fields})
    assert response.status_code in (200, 201), response.text
    return response.json()  # type: ignore[no-any-return]


async def move(api: httpx2.AsyncClient, app_id: str, status: str, **extra: Any) -> httpx2.Response:
    return await api.post(f"{APPS}/{app_id}/status", json={"status": status, **extra})


async def ok(response: httpx2.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def prepared(api: httpx2.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A tracked job with a verified tailored resume, moved to 'application prepared'."""
    ids = await build_profile(api)
    job_id = await build_job(api)
    resume = await post(api, f"{JOBS}/{job_id}/tailored-resumes")
    app = await track(api, job_id)
    assert app["resume"]["id"] == resume["id"]  # the latest resume is attached
    app = await ok(await move(api, app["id"], "application_prepared"))
    return app, ids


# --- Tracking and the lifecycle -------------------------------------------------------------


async def test_tracking_a_job_records_what_the_tracker_needs(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    response = await api.post(APPS, json={"job_id": job_id, "notes": "Referral from Asha."})
    assert response.status_code == 201
    app = response.json()
    assert (app["company"], app["position"]) == ("Northwind", "ML Engineer")
    assert app["status"] == "saved" and app["discovered_at"] and app["applied_at"] is None
    assert app["notes"] == "Referral from Asha."
    assert app["resume"] is None and app["cover_letter"] is None and app["answers"] == []
    titles = [e["title"] for e in app["timeline"]]
    assert "Started tracking as Saved" in titles
    assert not any(t.startswith("Moved to") for t in titles)  # the start isn't repeated
    again = await api.post(APPS, json={"job_id": job_id})
    assert again.status_code == 200 and again.json()["id"] == app["id"]


async def test_the_full_lifecycle_with_approval_and_reminders(api: httpx2.AsyncClient) -> None:
    app, _ = await prepared(api)
    app_id = app["id"]

    # Submission is impossible without the candidate's approval.
    refused = await move(api, app_id, "submitted")
    assert refused.status_code == 409 and "Approve the application first" in refused.text
    assert (await move(api, app_id, "interview")).status_code == 409

    approved = await ok(await api.post(f"{APPS}/{app_id}/approve"))
    assert approved["approved_at"] and approved["status"] == "awaiting_approval"
    assert approved["resume"]["status"] == "approved"  # the documents are locked in
    assert approved["timeline"][0]["title"] in ("Approved by you", "Moved to Awaiting approval")
    assert "submitted" in approved["allowed_statuses"]
    assert (await api.post(f"{APPS}/{app_id}/approve")).status_code == 409  # already approved

    applied_on = datetime.now(UTC).date().isoformat()
    submitted = await ok(
        await move(
            api, app_id, "submitted", submitted_on=applied_on, note="Applied on the company site."
        )
    )
    assert submitted["applied_at"].startswith(applied_on)
    [reminder] = submitted["follow_ups"]
    assert reminder["subject"] == "Check in on your application" and reminder["status"] == "pending"
    due = datetime.fromisoformat(reminder["due_at"])
    assert (
        timedelta(days=6)
        < due - datetime.fromisoformat(submitted["applied_at"])
        <= timedelta(days=7)
    )
    assert (await move(api, app_id, "saved")).status_code == 409  # no way back

    when = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    interviewing = await ok(
        await api.post(
            f"{APPS}/{app_id}/interviews",
            json={
                "interview_type": "technical",
                "scheduled_at": when,
                "duration_minutes": 60,
                "meeting_url": "https://meet.example.com/abc",
            },
        )
    )
    assert interviewing["status"] == "interview"  # moved automatically, and says so
    assert any(e["title"] == "Moved to Interview (automatic)" for e in interviewing["timeline"])
    [interview] = interviewing["interviews"]
    assert interviewing["next_interview_at"] is not None

    completed = await ok(
        await api.patch(
            f"{APPS}/{app_id}/interviews/{interview['id']}", json={"status": "completed"}
        )
    )
    [event] = [e for e in completed["timeline"] if e["kind"] == "interview"]
    assert event["title"] == "Interview (technical): completed" and not event["upcoming"]
    thanks = [f for f in completed["follow_ups"] if f["subject"] == "Send a thank-you note"]
    assert len(thanks) == 1 and thanks[0]["interview_id"] == interview["id"]

    done = await ok(
        await api.patch(f"{APPS}/{app_id}/follow-ups/{thanks[0]['id']}", json={"status": "done"})
    )
    assert any(e["title"] == "Followed up: Send a thank-you note" for e in done["timeline"])

    offer = await ok(await move(api, app_id, "offer", note="Verbal offer."))
    kinds = {e["kind"] for e in offer["timeline"]}
    assert {"created", "status", "approval", "document", "interview", "follow_up"} <= kinds
    titles = [e["title"] for e in offer["timeline"]]
    assert "Moved to Offer" in titles and "Moved to Submitted" in titles


async def test_approval_needs_verified_documents_and_approved_answers(
    api: httpx2.AsyncClient,
) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    app = await track(api, job_id)
    early = await api.post(f"{APPS}/{app['id']}/approve")
    assert early.status_code == 409 and "Application prepared" in early.text
    await move(api, app["id"], "application_prepared")
    missing = await api.post(f"{APPS}/{app['id']}/approve")
    assert missing.status_code == 409 and "Attach a tailored resume" in missing.text

    resume = await post(api, f"{JOBS}/{job_id}/tailored-resumes")
    await ok(await api.patch(f"{APPS}/{app['id']}", json={"tailored_resume_id": resume["id"]}))
    await post(
        api,
        f"{JOBS}/{job_id}/application-answers",
        {"questions": ["Describe your experience with Python."]},
    )
    blocked = await api.post(f"{APPS}/{app['id']}/approve")
    assert blocked.status_code == 409 and "0 of 1 approved" in blocked.text

    detail = (await api.get(f"{APPS}/{app['id']}")).json()
    [answer] = detail["answers"]
    assert detail["answers_total"] == 1 and not answer["approved"]
    await post(api, f"/api/v1/application-answers/{answer['id']}/approve")
    approved = await ok(await api.post(f"{APPS}/{app['id']}/approve"))
    assert approved["approval_blockers"] == ["Already approved."]
    assert all(item["ok"] for item in approved["readiness"])


async def test_interviews_need_a_submission_first(api: httpx2.AsyncClient) -> None:
    app, _ = await prepared(api)
    response = await api.post(
        f"{APPS}/{app['id']}/interviews", json={"interview_type": "phone_screen"}
    )
    assert response.status_code == 409 and "submitted" in response.text


async def test_submission_date_cannot_precede_approval(api: httpx2.AsyncClient) -> None:
    app, _ = await prepared(api)
    await ok(await api.post(f"{APPS}/{app['id']}/approve"))
    response = await move(api, app["id"], "submitted", submitted_on="2020-01-01")
    assert response.status_code == 422 and "before you approved" in response.text


# --- Documents ------------------------------------------------------------------------------


async def test_documents_used_are_kept_and_protected(api: httpx2.AsyncClient) -> None:
    app, _ = await prepared(api)
    first = app["resume"]
    job_id = app["job_id"]
    second = await post(api, f"{JOBS}/{job_id}/tailored-resumes")  # regenerate
    assert second["version"] == first["version"] + 1
    detail = (await api.get(f"{APPS}/{app['id']}")).json()
    assert detail["resume"]["id"] == first["id"]  # the attached version survives regeneration
    assert detail["resume"]["newer_version"] == second["version"]
    blocked = await api.delete(f"/api/v1/tailored-resumes/{first['id']}")
    assert blocked.status_code == 409 and "attached to an application" in blocked.text

    other_job = await make_job(api, "Data Engineer", "Bluefin Retail", "Pune")
    other = await post(api, f"{JOBS}/{other_job}/tailored-resumes")
    wrong = await api.patch(f"{APPS}/{app['id']}", json={"tailored_resume_id": other["id"]})
    assert wrong.status_code == 422

    switched = await ok(
        await api.patch(f"{APPS}/{app['id']}", json={"tailored_resume_id": second["id"]})
    )
    assert switched["resume"]["id"] == second["id"] and switched["resume"]["newer_version"] is None
    await ok(await api.post(f"{APPS}/{app['id']}/approve"))
    locked = await api.patch(f"{APPS}/{app['id']}", json={"tailored_resume_id": None})
    assert locked.status_code == 409


# --- Dashboard, filters, search -------------------------------------------------------------


async def test_dashboard_filters_and_search(api: httpx2.AsyncClient, db: AsyncSession) -> None:
    await post(api, PROFILE, {"full_name": "Test Candidate"})
    northwind = await track(
        api,
        await make_job(api, "ML Engineer", "Northwind", "Remote"),
        notes="Great team, referral from Asha.",
    )
    bluefin = await track(api, await make_job(api, "Data Analyst", "Bluefin Retail", "Bengaluru"))
    await track(
        api,
        await make_job(api, "Backend Engineer", "Ledgerly", "Hyderabad"),
        status="application_prepared",
    )
    await ok(await move(api, bluefin["id"], "withdrawn", note="Role filled internally."))
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await ok(
        await api.post(
            f"{APPS}/{northwind['id']}/follow-ups",
            json={"due_at": yesterday, "subject": "Email the recruiter", "channel": "email"},
        )
    )

    async def names(**params: Any) -> list[str]:
        response = await api.get(APPS, params=params)
        assert response.status_code == 200, response.text
        return [a["company"] for a in response.json()]

    assert sorted(await names()) == ["Bluefin Retail", "Ledgerly", "Northwind"]
    assert await names(status="withdrawn") == ["Bluefin Retail"]
    assert sorted(await names(status=["saved", "application_prepared"])) == [
        "Ledgerly",
        "Northwind",
    ]
    assert await names(q="ledger") == ["Ledgerly"]
    assert await names(q="analyst") == ["Bluefin Retail"]
    assert await names(q="asha") == ["Northwind"]  # notes are searched
    assert await names(q="hyderabad") == ["Ledgerly"]
    assert await names(follow_up_due="true") == ["Northwind"]
    assert await names(sort="company") == ["Bluefin Retail", "Ledgerly", "Northwind"]
    assert (await api.get(APPS, params={"status": "draft"})).status_code == 422

    board = (await api.get(f"{APPS}/dashboard")).json()
    assert board["total"] == 3 and board["active"] == 2
    assert board["counts"]["withdrawn"] == 1 and board["counts"]["application_prepared"] == 1
    [due] = board["follow_ups_due"]
    assert (
        due["overdue"] and due["company"] == "Northwind" and due["subject"] == "Email the recruiter"
    )
    assert {r["company"] for r in board["recent"]} == {"Northwind", "Bluefin Retail", "Ledgerly"}
    summary = next(a for a in (await api.get(APPS)).json() if a["company"] == "Northwind")
    assert summary["overdue_follow_ups"] == 1


async def test_follow_ups_can_be_skipped_rescheduled_and_deleted(api: httpx2.AsyncClient) -> None:
    await post(api, PROFILE, {"full_name": "Test Candidate"})
    app = await track(api, await make_job(api, "ML Engineer", "Northwind", "Remote"))
    later = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    added = await ok(
        await api.post(
            f"{APPS}/{app['id']}/follow-ups",
            json={"due_at": later, "subject": "Ask about timeline"},
        )
    )
    [follow_up] = added["follow_ups"]
    moved_to = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    rescheduled = await ok(
        await api.patch(
            f"{APPS}/{app['id']}/follow-ups/{follow_up['id']}", json={"due_at": moved_to}
        )
    )
    assert rescheduled["follow_ups"][0]["due_at"][:10] == moved_to[:10]
    skipped = await ok(
        await api.patch(
            f"{APPS}/{app['id']}/follow-ups/{follow_up['id']}", json={"status": "skipped"}
        )
    )
    assert skipped["follow_ups"][0]["status"] == "skipped" and skipped["next_follow_up_at"] is None
    gone = await ok(await api.delete(f"{APPS}/{app['id']}/follow-ups/{follow_up['id']}"))
    assert gone["follow_ups"] == []


async def test_deleting_an_application_frees_the_job(api: httpx2.AsyncClient) -> None:
    await post(api, PROFILE, {"full_name": "Test Candidate"})
    job_id = await make_job(api, "ML Engineer", "Northwind", "Remote")
    app = await track(api, job_id)
    assert (await api.delete(f"{JOBS}/{job_id}")).status_code == 409
    assert (await api.delete(f"{APPS}/{app['id']}")).status_code == 204
    assert (await api.delete(f"{JOBS}/{job_id}")).status_code == 204


async def test_applications_are_private(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    async with _client(db, user, embedder) as api:
        await post(api, PROFILE, {"full_name": "Test Candidate"})
        job_id = await make_job(api, "ML Engineer", "Northwind", "Remote")
        app = await track(api, job_id)
    other = await make_user(db, "other@example.test")
    async with _client(db, other, embedder) as stranger:
        await post(stranger, PROFILE, {"full_name": "Other"})
        assert (await stranger.get(APPS)).json() == []
        assert (await stranger.post(APPS, json={"job_id": job_id})).status_code == 404
        for method, path, body in (
            ("GET", f"{APPS}/{app['id']}", None),
            ("PATCH", f"{APPS}/{app['id']}", {"notes": "x"}),
            ("POST", f"{APPS}/{app['id']}/status", {"status": "saved"}),
            ("POST", f"{APPS}/{app['id']}/approve", None),
            (
                "POST",
                f"{APPS}/{app['id']}/follow-ups",
                {"due_at": datetime.now(UTC).isoformat(), "subject": "x"},
            ),
            ("DELETE", f"{APPS}/{app['id']}", None),
        ):
            response = await stranger.request(method, path, json=body)
            assert response.status_code == 404, (method, path)
