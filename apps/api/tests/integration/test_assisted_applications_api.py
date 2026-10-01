"""The browser-assisted workflow, end to end, against the mock application site in a real
headless browser. Proves nothing reaches the site before the candidate's confirmation."""

import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx2
import pytest
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.matching import get_match_llm
from app.automation import mock_site
from app.core.config import Settings
from app.users.models import User

from .conftest import client_for, make_user
from .test_applications_api import APPS, approve, move, ok, track
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, build_job, post
from .test_tailored_resume_api import build_profile

pytestmark = pytest.mark.anyio
RUNS = "/api/v1/assisted-runs"
Q_INTEREST = "Why are you interested in this role?"
Q_PYTHON = "Describe your experience with Python."


@pytest.fixture(scope="session")
def site() -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(mock_site.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the mock site didn't start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


@pytest.fixture(autouse=True)
def _clean_site() -> Iterator[None]:
    mock_site.SUBMISSIONS.clear()
    mock_site.QUESTIONS.clear()
    mock_site.BEACONS.clear()
    yield
    mock_site.SUBMISSIONS.clear()
    mock_site.QUESTIONS.clear()
    mock_site.BEACONS.clear()


def _client(db: AsyncSession, user: User, site: str) -> httpx2.AsyncClient:
    embedder = SpyEmbedder()
    overrides: dict[Any, Any] = {get_embedder: lambda: embedder, get_match_llm: lambda: None}
    settings = Settings(
        _env_file=None, app_env="test", automation_mock_site_url=site, automation_timeout_ms=10000
    )
    return client_for(db, user, settings=settings, overrides=overrides)


@pytest.fixture
async def api(db: AsyncSession, user: User, site: str) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, site) as client:
        yield client


async def approved_application(
    api: httpx2.AsyncClient,
    url: str,
    *,
    questions: tuple[str, ...] = (Q_INTEREST, Q_PYTHON),
    letter: bool = True,
) -> dict[str, Any]:
    """An approved application with an approved resume, cover letter and answers."""
    await build_profile(api)
    job_id = await build_job(api)
    await post(api, f"{JOBS}/{job_id}/tailored-resumes")
    if letter:
        await post(api, f"{JOBS}/{job_id}/cover-letters")
    app = await track(api, job_id)
    await ok(await api.patch(f"{APPS}/{app['id']}", json={"application_url": url}))
    if questions:
        answers = await post(
            api, f"{JOBS}/{job_id}/application-answers", {"questions": list(questions)}
        )
        for answer in answers if isinstance(answers, list) else answers["answers"]:
            await post(api, f"/api/v1/application-answers/{answer['id']}/approve")
    await ok(await move(api, app["id"], "application_prepared"))
    await ok(await approve(api, app["id"]))
    return await ok(await api.get(f"{APPS}/{app['id']}"))


async def start(api: httpx2.AsyncClient, app_id: str, **inputs: str) -> dict[str, Any]:
    response = await api.post(f"{APPS}/{app_id}/assisted-runs", json={"inputs": inputs})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def actions(run: dict[str, Any]) -> list[str]:
    return [e["action"] for e in run["events"]]


# --- The happy path -------------------------------------------------------------------------


async def test_fills_pauses_for_review_and_submits_only_after_confirmation(
    api: httpx2.AsyncClient, site: str
) -> None:
    app = await approved_application(api, f"{site}/jobs/northwind-ml/apply")

    # 1. Fill: work authorization isn't in the candidate's records, so it asks.
    run = await start(api, app["id"])
    assert run["status"] == "needs_input" and run["review"] is None
    [problem] = run["problems"]
    assert problem["field_id"] == "work_authorization" and problem["needs_input"]
    assert problem["options"] == ["Yes", "No"]
    assert mock_site.SUBMISSIONS == []

    bad = await ok(
        await api.post(f"{RUNS}/{run['id']}/inputs", json={"inputs": {"work_authorization": "?"}})
    )
    assert bad["status"] == "needs_input" and "Choose one of" in bad["problems"][0]["message"]

    # 2. Provide it: the form is filled, read back and paused. Nothing is sent.
    run = await ok(
        await api.post(f"{RUNS}/{run['id']}/inputs", json={"inputs": {"work_authorization": "Yes"}})
    )
    assert run["status"] == "awaiting_review", run["stop_reason"]
    assert run["nothing_submitted"] and mock_site.SUBMISSIONS == []
    review = run["review"]
    assert review["destination"]["host"] == site.removeprefix("http://")
    assert review["destination"]["form_action"] == f"{site}/jobs/northwind-ml/submit"
    assert review["personal"]["first_name"] == "Test"
    assert review["personal"]["last_name"] == "Candidate"
    assert review["personal"]["email"] == "t@example.test"
    assert review["resume"]["file_name"] == "Test-Candidate-resume-v1.pdf"
    assert review["resume"]["size_bytes"] > 1000 and len(review["resume"]["sha256"]) == 64
    assert review["cover_letter"]["file_name"] == "Test-Candidate-cover-letter-v1.pdf"
    assert [a["question"] for a in review["answers"]] == [Q_INTEREST, Q_PYTHON]
    assert all(a["answer"] for a in review["answers"])
    assert review["additional_fields"] == [
        {
            "field_id": "work_authorization",
            "label": "Are you authorized to work in India?",
            "value": "Yes",
            "source": "you provided this",
        }
    ]
    assert "How did you hear about us?" in review["left_blank"]
    assert {"open_page", "detected_fields", "filled_fields", "attached_resume"} <= set(actions(run))
    assert {"attached_cover_letter", "filled_answers", "paused_for_review"} <= set(actions(run))
    assert "submitted" not in actions(run)

    # 3. No confirmation, or a stale review: refused, still nothing sent.
    unconfirmed = await api.post(
        f"{RUNS}/{run['id']}/submit", json={"review_hash": run["review_hash"], "confirm": False}
    )
    assert unconfirmed.status_code == 422
    stale = await api.post(
        f"{RUNS}/{run['id']}/submit", json={"review_hash": "0" * 64, "confirm": True}
    )
    assert stale.status_code == 409 and "Review it again" in stale.text
    assert mock_site.SUBMISSIONS == []

    # 4. Confirm: the site receives exactly what the review showed.
    done = await ok(
        await api.post(
            f"{RUNS}/{run['id']}/submit",
            json={"review_hash": run["review_hash"], "confirm": True},
        )
    )
    assert done["status"] == "submitted" and not done["nothing_submitted"]
    assert done["confirmed_at"] and done["submitted_at"]
    [sent] = mock_site.SUBMISSIONS
    assert done["confirmation_reference"] == sent["reference"]
    fields = sent["fields"]
    assert fields["first_name"] == "Test" and fields["last_name"] == "Candidate"
    assert fields["email"] == "t@example.test" and fields["work_authorization"] == "Yes"
    assert fields["q1"] == review["answers"][0]["answer"]
    assert fields["q2"] == review["answers"][1]["answer"]
    assert fields["referral_source"] == ""
    assert sent["files"]["resume"] == {
        "file_name": review["resume"]["file_name"],
        "size": review["resume"]["size_bytes"],
        "sha256": review["resume"]["sha256"],
    }
    assert sent["files"]["cover_letter"]["sha256"] == review["cover_letter"]["sha256"]
    log = actions(done)
    assert log.index("confirmed") < log.index("verified_review") < log.index("submit_clicked")
    assert log[-2:] == ["submitted", "browser_closed"]
    [confirmed] = [e for e in done["events"] if e["action"] == "confirmed"]
    assert confirmed["actor"] == "user" and confirmed["detail"]["review_hash"] == run["review_hash"]

    # 5. The application is submitted, by automation, after the candidate's confirmation.
    tracked = await ok(await api.get(f"{APPS}/{app['id']}"))
    assert tracked["status"] == "submitted" and tracked["applied_at"]
    assert any(
        "after your explicit confirmation" in (e["detail"] or "") for e in tracked["timeline"]
    )
    assert any(f["subject"] == "Check in on your application" for f in tracked["follow_ups"])

    # 6. It can't be submitted twice.
    again = await api.post(
        f"{RUNS}/{run['id']}/submit", json={"review_hash": run["review_hash"], "confirm": True}
    )
    assert again.status_code == 409 and len(mock_site.SUBMISSIONS) == 1
    listed = (await api.get(f"{APPS}/{app['id']}/assisted-runs")).json()
    assert [r["id"] for r in listed] == [run["id"]]


async def test_the_form_changing_after_review_stops_submission(
    api: httpx2.AsyncClient, site: str
) -> None:
    app = await approved_application(api, f"{site}/jobs/changing/apply")
    run = await start(api, app["id"], work_authorization="No")
    assert run["status"] == "awaiting_review", run["stop_reason"]
    mock_site.QUESTIONS["changing"] = [
        ("q1", Q_INTEREST, True),
        ("q2", Q_PYTHON, False),
        ("q3", "What is your notice period?", False),
    ]
    stopped = await ok(
        await api.post(
            f"{RUNS}/{run['id']}/submit",
            json={"review_hash": run["review_hash"], "confirm": True},
        )
    )
    assert stopped["status"] == "stopped" and "no longer matches" in stopped["stop_reason"]
    assert stopped["nothing_submitted"] and mock_site.SUBMISSIONS == []
    assert "submit_clicked" not in actions(stopped)
    tracked = await ok(await api.get(f"{APPS}/{app['id']}"))
    assert tracked["status"] == "awaiting_approval" and tracked["applied_at"] is None


async def test_a_required_question_without_an_approved_answer_stops_for_the_candidate(
    api: httpx2.AsyncClient, site: str
) -> None:
    app = await approved_application(
        api, f"{site}/jobs/northwind-ml/apply", questions=(), letter=False
    )
    run = await start(api, app["id"], work_authorization="Yes")
    assert run["status"] == "needs_input"
    [problem] = run["problems"]
    assert problem["field_id"] == "q1" and not problem["needs_input"]
    assert "approve the answer" in problem["message"]
    assert mock_site.SUBMISSIONS == []


# --- Stops: never bypassed ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "action", "phrase"),
    [
        ("captcha/x/apply", "access_control", "CAPTCHA"),
        ("secure/x/apply", "access_control", "sign in"),
        ("blocked/x/apply", "access_control", "refused access"),
        ("ratelimited/x/apply", "access_control", "rate limiting"),
        ("changed/x/apply", "unsupported_form", "recognises"),
        ("autosubmit/x/apply", "unsafe_page", "tried to send"),
        ("popup/x/apply", "unsafe_page", "tried to send"),
        ("redirect/x/apply", "redirected", "another site"),
    ],
)
async def test_access_controls_and_unknown_forms_stop_with_an_explanation(
    api: httpx2.AsyncClient, site: str, path: str, action: str, phrase: str
) -> None:
    app = await approved_application(api, f"{site}/{path}", letter=False)
    run = await start(api, app["id"], work_authorization="Yes")
    assert run["status"] == "stopped", run
    assert phrase.lower() in run["stop_reason"].lower()
    assert action in actions(run) and run["review"] is None
    assert mock_site.SUBMISSIONS == []
    refused = await api.post(
        f"{RUNS}/{run['id']}/submit", json={"review_hash": "0" * 64, "confirm": True}
    )
    assert refused.status_code == 409


async def test_unsupported_sites_are_not_opened(api: httpx2.AsyncClient) -> None:
    app = await approved_application(api, "https://careers.example.com/apply/123", letter=False)
    run = await start(api, app["id"])
    assert run["status"] == "stopped" and run["adapter"] is None
    assert "isn't a supported application site" in run["stop_reason"]
    assert actions(run) == ["started", "unsupported_site"]


async def test_the_mock_site_is_never_supported_in_production(
    db: AsyncSession, user: User, site: str
) -> None:
    from app.automation.adapters import adapter_for

    production = Settings(_env_file=None, app_env="production", automation_mock_site_url=site)
    assert adapter_for(f"{site}/jobs/x/apply", production) is None
    test = Settings(_env_file=None, app_env="test", automation_mock_site_url=site)
    assert adapter_for(f"{site}/jobs/x/apply", test) is not None


# --- Preconditions and privacy --------------------------------------------------------------


async def test_only_approved_applications_can_be_assisted(
    api: httpx2.AsyncClient, site: str
) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    app = await track(api, job_id)
    response = await api.post(f"{APPS}/{app['id']}/assisted-runs", json={})
    assert response.status_code == 409 and "Approve the application first" in response.text


async def test_cancel_and_restart(api: httpx2.AsyncClient, site: str) -> None:
    app = await approved_application(api, f"{site}/jobs/northwind-ml/apply", letter=False)
    first = await start(api, app["id"])
    second = await start(api, app["id"], work_authorization="Yes")
    assert (await api.get(f"{RUNS}/{first['id']}")).json()["status"] == "cancelled"
    cancelled = await ok(await api.post(f"{RUNS}/{second['id']}/cancel"))
    assert cancelled["status"] == "cancelled" and actions(cancelled)[-1] == "cancelled"
    refused = await api.post(
        f"{RUNS}/{second['id']}/submit",
        json={"review_hash": second["review_hash"], "confirm": True},
    )
    assert refused.status_code == 409 and mock_site.SUBMISSIONS == []


async def test_runs_are_private(api: httpx2.AsyncClient, db: AsyncSession, site: str) -> None:
    app = await approved_application(api, "https://careers.example.com/apply/1", letter=False)
    run = await start(api, app["id"])
    stranger = await make_user(db, "stranger@example.test")
    async with _client(db, stranger, site) as other:
        await post(other, "/api/v1/profile", {"full_name": "Someone Else"})
        assert (await other.get(f"{RUNS}/{run['id']}")).status_code == 404
        assert (await other.post(f"{RUNS}/{run['id']}/cancel")).status_code == 404
        assert (await other.get(f"{APPS}/{app['id']}/assisted-runs")).status_code == 404


async def test_a_change_after_approval_stops_assisted_submission(
    api: httpx2.AsyncClient, site: str
) -> None:
    app = await approved_application(api, f"{site}/jobs/northwind-ml/apply", letter=False)
    run = await start(api, app["id"], work_authorization="Yes")
    assert run["status"] == "awaiting_review", run["stop_reason"]
    await ok(await api.patch("/api/v1/profile", json={"location": "Bengaluru"}))
    refused = await api.post(
        f"{RUNS}/{run['id']}/submit", json={"review_hash": run["review_hash"], "confirm": True}
    )
    assert refused.status_code == 409 and "changed after you approved it" in refused.text
    assert mock_site.SUBMISSIONS == []
    tracked = await ok(await api.get(f"{APPS}/{app['id']}"))
    assert tracked["approval_state"] == "ready_for_review" and tracked["applied_at"] is None
    again = await api.post(f"{APPS}/{app['id']}/assisted-runs", json={"inputs": {}})
    assert again.status_code == 409 and "Approve the application first" in again.text


async def test_a_page_cannot_send_the_filled_form_to_another_site(
    api: httpx2.AsyncClient, site: str
) -> None:
    app = await approved_application(api, f"{site}/exfil/x/apply", letter=False)
    run = await start(api, app["id"], work_authorization="Yes")
    assert run["status"] == "awaiting_review", run["stop_reason"]
    assert mock_site.BEACONS == []  # the filled email never left the page
    assert "blocked_external" in actions(run)
    assert all("?" not in str(e["detail"].get("url", "")) for e in run["events"])
