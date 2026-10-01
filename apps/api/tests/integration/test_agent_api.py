"""The agent end to end, on the real services: stage transitions, pauses for a human,
the execution log, secrets kept out of it, and authorization."""

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import tools
from app.agent.machine import Stage
from app.agent.models import AgentActionLog, AgentRun
from app.api.routes.agent import get_agent_llm
from app.api.routes.application_answers import get_answer_llm
from app.api.routes.candidate_evidence import get_embedder
from app.api.routes.cover_letters import get_letter_llm
from app.api.routes.jobs import get_job_llm
from app.api.routes.matching import get_match_llm
from app.api.routes.tailored_resumes import get_tailor_llm
from app.api.routes.verification import get_verification_llm
from app.core.config import Settings
from app.documents.models import ClaimStatus, GeneratedClaim
from app.users.dependencies import get_current_user
from app.users.models import User

from .conftest import client_for, make_user
from .test_applications_api import APPS, approve, make_job, move, ok
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, build_job, post
from .test_tailored_resume_api import build_profile

pytestmark = pytest.mark.anyio
RUNS = "/api/v1/agent/runs"
SECRET = "configured-anthropic-key-0000-SECRET"  # noqa: S105 - a fake, to prove redaction


def _client(db: AsyncSession, user: User, **settings: Any) -> httpx2.AsyncClient:
    embedder = SpyEmbedder()
    # A (fake) API key is configured to prove it never reaches the logs, so no route may
    # build a real AI provider from it: every LLM dependency is replaced.
    overrides: dict[Any, Any] = {get_embedder: lambda: embedder} | {
        llm: lambda: None
        for llm in (
            get_agent_llm,
            get_answer_llm,
            get_job_llm,
            get_letter_llm,
            get_match_llm,
            get_tailor_llm,
            get_verification_llm,
        )
    }
    config = Settings(
        _env_file=None, app_env="test", anthropic_api_key=SecretStr(SECRET), **settings
    )
    return client_for(db, user, settings=config, overrides=overrides)


@pytest.fixture
async def api(db: AsyncSession, user: User) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user) as client:
        yield client


async def start(api: httpx2.AsyncClient, **goal: Any) -> dict[str, Any]:
    response = await api.post(RUNS, json=goal)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def advance(api: httpx2.AsyncClient, run_id: str, **body: Any) -> dict[str, Any]:
    return await ok(await api.post(f"{RUNS}/{run_id}/advance", json=body))


def tools_called(run: dict[str, Any]) -> list[str]:
    return [e["tool"] for e in run["log"]]


# --- The full workflow ----------------------------------------------------------------------


async def test_the_agent_walks_the_state_machine_and_waits_for_the_human(
    api: httpx2.AsyncClient,
) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    run = await start(api, job_id=job_id, questions=["Describe your experience with Python."])
    assert (run["stage"], run["status"]) == ("discover", "ready")
    assert tools_called(run) == ["start"]  # nothing runs until advanced

    # DISCOVER → ANALYZE → MATCH, then stop: eligibility is uncertain.
    run = await advance(api, run["id"])
    assert (run["stage"], run["status"]) == ("match", "waiting_for_human")
    assert run["pause"]["kind"] == "eligibility_uncertain"
    assert run["pause"]["items"] == [
        "May disqualify you: Must be authorized to work in the United States",
        "Can't tell from your profile: Excellent written communication",
    ]
    assert tools_called(run) == [
        "start",
        "check_profile",
        "select_job",
        "transition",
        "review_analysis",
        "transition",
        "compute_match",
        "pause",
    ]
    assert run["job_id"] == job_id and run["application_id"] is None

    # Advancing again without a decision changes nothing.
    again = await advance(api, run["id"])
    assert again["stage"] == "match" and again["pause"]["kind"] == "eligibility_uncertain"
    assert tools_called(again)[-2:] == ["compute_match", "pause"]
    assert again["log"][-2]["status"] == "skipped"  # the current match is reused

    # The candidate confirms: PREPARE → VERIFY → REVIEW, where the generated answer needs
    # their approval.
    run = await advance(api, run["id"], confirm_eligibility=True)
    assert (run["stage"], run["pause"]["kind"]) == ("review", "approval_required")
    assert run["pause"]["items"] == ["Describe your experience with Python."]
    [answer] = (await api.get(f"{JOBS}/{job_id}/application-answers")).json()
    await ok(await api.post(f"/api/v1/application-answers/{answer['id']}/approve"))

    # REVIEW → APPROVE, which needs the candidate's approval of the application.
    run = await advance(api, run["id"])
    assert (run["stage"], run["status"]) == ("approve", "waiting_for_human")
    assert run["pause"]["kind"] == "approval_required"
    assert "never approves for you" in run["pause"]["message"]
    called = tools_called(run)
    for tool in (
        "human_input",
        "tailor_resume",
        "write_cover_letter",
        "prepare_application",
        "check_claims",
        "request_review",
        "check_approval",
    ):
        assert tool in called
    app = await ok(await api.get(f"{APPS}/{run['application_id']}"))
    assert app["status"] == "awaiting_approval" and app["approval_state"] == "ready_for_review"
    assert app["resume"] and app["cover_letter"] and len(app["answers"]) == 1
    assert app["approved_at"] is None  # the agent never approves

    # Still waiting: the agent doesn't approve however often it's advanced.
    for _ in range(2):
        run = await advance(api, run["id"])
    assert run["stage"] == "approve" and run["pause"]["kind"] == "approval_required"
    assert (await ok(await api.get(f"{APPS}/{run['application_id']}")))["approved_at"] is None

    # The human approves; the agent moves to SUBMIT and waits again: it never submits.
    await ok(await approve(api, run["application_id"]))
    run = await advance(api, run["id"])
    assert (run["stage"], run["pause"]["kind"]) == ("submit", "approval_required")
    assert "never submits" in run["pause"]["message"]
    app = await ok(await api.get(f"{APPS}/{run['application_id']}"))
    assert app["applied_at"] is None

    # The human submits; the agent tracks it and completes.
    await ok(await move(api, run["application_id"], "submitted"))
    run = await advance(api, run["id"])
    assert (run["stage"], run["status"], run["pause"]) == ("done", "completed", None)
    assert all(s["state"] == "done" for s in run["stages"])
    assert tools_called(run)[-3:] == ["track_application", "transition", "complete"]
    closed = await api.post(f"{RUNS}/{run['id']}/advance", json={})
    assert closed.status_code == 409


async def test_every_action_is_logged_with_its_details(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    job_id = await make_job(api, "Data Engineer", "Bluefin Retail", "Pune")
    run = await start(api, job_id=job_id, include_cover_letter=False)
    run = await advance(api, run["id"], max_stages=3)
    assert run["stage"] == "prepare" and run["status"] == "ready"  # bounded: three stages
    for entry in run["log"]:
        assert entry["at"] and entry["task"] and entry["tool"] and entry["agent"]
        assert entry["status"] in ("succeeded", "skipped", "paused", "failed")
        assert entry["input_summary"] is not None and entry["duration_ms"] >= 0
    match = next(e for e in run["log"] if e["tool"] == "compute_match")
    assert match["agent"] == "matching" and match["stage"] == "match"
    assert match["input_summary"] == f"job {job_id}"
    assert "Evidence coverage" in match["output_summary"] and match["error"] is None
    transitions = [e["input_summary"] for e in run["log"] if e["tool"] == "transition"]
    assert transitions == ["discover → analyze", "analyze → match", "match → prepare"]
    times = [e["at"] for e in run["log"]]
    assert times == sorted(times) and len(set(times)) == len(times)


# --- Stopping for a human -------------------------------------------------------------------


async def test_missing_information_stops_the_agent(api: httpx2.AsyncClient) -> None:
    await post(api, "/api/v1/profile", {"full_name": "No Evidence Yet"})
    job_id = await make_job(api, "Data Engineer", "Bluefin Retail", "Pune")
    run = await advance(api, (await start(api, job_id=job_id))["id"])
    assert (run["stage"], run["status"]) == ("discover", "waiting_for_human")
    assert run["pause"]["kind"] == "missing_information"
    assert any("Confirmed evidence" in i for i in run["pause"]["items"])


async def test_failed_claim_verification_stops_the_agent(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await build_profile(api)
    job_id = await make_job(api, "Data Engineer", "Bluefin Retail", "Pune")
    run = await start(api, job_id=job_id, include_cover_letter=False)
    run = await advance(api, run["id"], max_stages=4)  # through PREPARE
    assert run["stage"] == "verify", run["pause"]
    app = await ok(await api.get(f"{APPS}/{run['application_id']}"))
    await db.execute(
        update(GeneratedClaim)
        .where(GeneratedClaim.tailored_resume_id == app["resume"]["id"])
        .where(GeneratedClaim.status == ClaimStatus.VERIFIED)
        .values(status=ClaimStatus.UNSUPPORTED)
    )
    run = await advance(api, run["id"])
    assert (run["stage"], run["pause"]["kind"]) == ("verify", "verification_failed")
    assert run["pause"]["items"] and run["pause"]["items"][0].startswith("Resume:")
    assert (await ok(await api.get(f"{APPS}/{run['application_id']}")))[
        "approval_state"
    ] == "draft"  # not sent for review


async def test_unanswerable_questions_are_ambiguous_fields(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    job_id = await make_job(api, "Data Engineer", "Bluefin Retail", "Pune")
    run = await start(
        api,
        job_id=job_id,
        include_cover_letter=False,
        questions=["What is your expected salary?"],
    )
    run = await advance(api, run["id"])
    assert (run["stage"], run["pause"]["kind"]) == ("verify", "ambiguous_fields")
    assert run["pause"]["items"] == ["What is your expected salary?"]

    # The candidate removes the question (or answers it themselves); the agent continues.
    [answer] = (await api.get(f"{JOBS}/{job_id}/application-answers")).json()
    assert (await api.delete(f"/api/v1/application-answers/{answer['id']}")).status_code == 204
    run = await advance(api, run["id"])
    assert (run["stage"], run["pause"]["kind"]) == ("approve", "approval_required")


async def test_service_refusals_pause_rather_than_fail(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    run = await advance(api, (await start(api, source="mock", external_id="no-such-posting"))["id"])
    assert run["stage"] == "discover" and run["status"] == "waiting_for_human"
    assert run["pause"]["kind"] == "missing_information"
    failed = next(e for e in run["log"] if e["tool"] == "select_job")
    assert failed["status"] == "failed" and failed["error"]


# --- Limits, failures and secrets -----------------------------------------------------------


async def test_unexpected_errors_fail_the_run_without_leaking_secrets(
    api: httpx2.AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await build_profile(api)
    job_id = await make_job(api, "Data Engineer", "Bluefin Retail", "Pune")
    run = await start(api, job_id=job_id)

    async def broken(ctx: tools.Ctx) -> tools.Result:
        raise RuntimeError(f"provider rejected key {SECRET} (x-api-key: sk-ant-api03-ZZZZZZZZZZ)")

    original = tools.BY_STAGE[Stage.ANALYZE]
    monkeypatch.setitem(
        tools.BY_STAGE,
        Stage.ANALYZE,
        [tools.Tool("review_analysis", original[0].agent, original[0].stage, "x", broken)],
    )
    failed = await advance(api, run["id"])
    assert (failed["stage"], failed["status"]) == ("analyze", "failed")
    assert "RuntimeError" in failed["last_error"] and "[REDACTED]" in failed["last_error"]

    monkeypatch.setitem(tools.BY_STAGE, Stage.ANALYZE, original)
    retried = await advance(api, run["id"], max_stages=1)  # a failed run can be retried
    assert (retried["stage"], retried["status"]) == ("match", "ready")

    stored = " ".join(
        f"{r.task} {r.input_summary} {r.output_summary} {r.error}"
        for r in await db.scalars(select(AgentActionLog))
    )
    run_row = await db.scalar(select(AgentRun))
    assert run_row is not None
    for leaked in (SECRET, "ZZZZZZZZZZ"):
        assert leaked not in stored and leaked not in (run_row.last_error or "")


async def test_secrets_in_inputs_are_redacted_from_the_log(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await build_profile(api)
    run = await start(api, source="mock", external_id=SECRET)
    run = await advance(api, run["id"])
    stored = " ".join(
        f"{r.input_summary} {r.output_summary} {r.error}"
        for r in await db.scalars(select(AgentActionLog))
    )
    assert SECRET not in stored and "[REDACTED]" in stored
    assert SECRET not in str(run["pause"])


async def test_runs_have_a_tool_call_limit(api: httpx2.AsyncClient, db: AsyncSession) -> None:
    await build_profile(api)
    job_id = await make_job(api, "Data Engineer", "Bluefin Retail", "Pune")
    run = await start(api, job_id=job_id)
    await db.execute(update(AgentRun).values(steps=59))
    limited = await advance(api, run["id"])
    assert limited["status"] == "failed" and "step limit" in limited["last_error"]


async def test_the_database_keeps_run_states_consistent(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    from sqlalchemy.exc import IntegrityError

    await build_profile(api)
    run = await start(api, job_id=await make_job(api, "Data Engineer", "Bluefin", "Pune"))
    with pytest.raises(IntegrityError, match="paused_has_reason"):
        await db.execute(
            text("UPDATE agent_runs SET status = 'waiting_for_human' WHERE id = :id"),
            {"id": run["id"]},
        )


# --- Authorization --------------------------------------------------------------------------


async def test_runs_are_private(api: httpx2.AsyncClient, db: AsyncSession) -> None:
    await build_profile(api)
    run = await start(api, job_id=await make_job(api, "Data Engineer", "Bluefin", "Pune"))
    stranger = await make_user(db, "stranger@example.test")
    async with _client(db, stranger) as other:
        await post(other, "/api/v1/profile", {"full_name": "Someone Else"})
        assert (await other.get(RUNS)).json() == []
        for method, path, body in (
            ("GET", f"{RUNS}/{run['id']}", None),
            ("POST", f"{RUNS}/{run['id']}/advance", {}),
            ("POST", f"{RUNS}/{run['id']}/cancel", None),
        ):
            assert (await other.request(method, path, json=body)).status_code == 404
        # Nor can a run be started on someone else's job.
        theirs = await advance(other, (await start(other, job_id=run["goal"]["job_id"]))["id"])
        assert theirs["status"] == "waiting_for_human" and theirs["job_id"] is None


async def test_requests_without_an_identity_are_refused(db: AsyncSession, user: User) -> None:
    production = Settings(_env_file=None, app_env="production", dev_user_email="me@localhost.dev")
    async with client_for(
        db, user, settings=production, overrides={get_current_user: get_current_user}
    ) as anonymous:
        assert (await anonymous.get(RUNS)).status_code == 401
        assert (await anonymous.post(RUNS, json={"job_id": str(JOBS)})).status_code in (401, 422)


async def test_cancelled_runs_stay_cancelled(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    run = await start(api, job_id=await make_job(api, "Data Engineer", "Bluefin", "Pune"))
    cancelled = await ok(await api.post(f"{RUNS}/{run['id']}/cancel"))
    assert cancelled["status"] == "cancelled"
    assert (await api.post(f"{RUNS}/{run['id']}/advance", json={})).status_code == 409


async def test_start_needs_exactly_one_target(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    assert (await api.post(RUNS, json={})).status_code == 422
    both = {"job_id": "00000000-0000-0000-0000-000000000000", "source": "mock", "external_id": "1"}
    assert (await api.post(RUNS, json=both)).status_code == 422
    assert (await api.post(RUNS, json={"job_id": both["job_id"], "x": 1})).status_code == 422
    tools_list = (await api.get("/api/v1/agent/tools")).json()
    assert len({t["agent"] for t in tools_list}) == 10
