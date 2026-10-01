"""The agent's state machine, tool registry and log redaction (no database)."""

import uuid
from dataclasses import replace

import pytest
from pydantic import SecretStr

from app.agent.machine import (
    NEXT,
    ORDER,
    Facts,
    PauseKind,
    Stage,
    TransitionError,
    decide,
    entry_check,
    exit_check,
    validate_transition,
)
from app.agent.models import AgentName
from app.agent.redact import REDACTED, redact, secrets_of
from app.agent.tools import BY_STAGE, TOOLS
from app.core.config import Settings

JOB, APP = uuid.uuid4(), uuid.uuid4()

# Facts as they are once each stage has done its work.
AFTER: dict[Stage, Facts] = {}
_f = Facts(job_id=JOB)
AFTER[Stage.DISCOVER] = _f
_f = replace(_f, requirements=5)
AFTER[Stage.ANALYZE] = _f
_f = replace(_f, matched=True)
AFTER[Stage.MATCH] = _f
_f = replace(_f, application_id=APP, resume_attached=True)
AFTER[Stage.PREPARE] = _f
AFTER[Stage.VERIFY] = _f
_f = replace(_f, review_state="ready_for_review")
AFTER[Stage.REVIEW] = _f
_f = replace(_f, review_state="approved", approval_current=True)
AFTER[Stage.APPROVE] = _f
_f = replace(_f, review_state="submitted", submitted=True)
AFTER[Stage.SUBMIT] = _f
_f = replace(_f, tracked=True)
AFTER[Stage.TRACK] = _f


def test_stages_run_in_the_specified_order() -> None:
    assert [s.value for s in ORDER] == [
        "discover",
        "analyze",
        "match",
        "prepare",
        "verify",
        "review",
        "approve",
        "submit",
        "track",
        "done",
    ]
    assert NEXT[Stage.DISCOVER] == Stage.ANALYZE and NEXT[Stage.TRACK] == Stage.DONE
    assert Stage.DONE not in NEXT


def test_a_complete_happy_path_moves_one_validated_stage_at_a_time() -> None:
    stage = Stage.DISCOVER
    visited = [stage]
    while stage != Stage.DONE:
        decision = decide(stage, AFTER[stage])
        assert decision.pause is None and decision.next_stage == NEXT[stage]
        stage = decision.next_stage
        visited.append(stage)
    assert visited == list(ORDER)


@pytest.mark.parametrize(
    ("stage", "facts", "kind", "phrase"),
    [
        (
            Stage.DISCOVER,
            Facts(profile_missing=["Your name"], job_id=JOB),
            PauseKind.MISSING_INFORMATION,
            "profile",
        ),
        (Stage.DISCOVER, Facts(), PauseKind.MISSING_INFORMATION, "Choose a job"),
        (Stage.ANALYZE, Facts(job_id=JOB), PauseKind.MISSING_INFORMATION, "no requirements"),
        (
            Stage.ANALYZE,
            replace(AFTER[Stage.ANALYZE], deadline_passed=True),
            PauseKind.ELIGIBILITY_UNCERTAIN,
            "deadline",
        ),
        (Stage.MATCH, AFTER[Stage.ANALYZE], PauseKind.MISSING_INFORMATION, "match"),
        (
            Stage.MATCH,
            replace(AFTER[Stage.MATCH], disqualifying=["5 years of Go"]),
            PauseKind.ELIGIBILITY_UNCERTAIN,
            "eligibility is uncertain",
        ),
        (
            Stage.MATCH,
            replace(AFTER[Stage.MATCH], unknown_required=["Work authorization"]),
            PauseKind.ELIGIBILITY_UNCERTAIN,
            "eligibility is uncertain",
        ),
        (Stage.PREPARE, AFTER[Stage.MATCH], PauseKind.MISSING_INFORMATION, "tailored resume"),
        (
            Stage.VERIFY,
            replace(AFTER[Stage.PREPARE], missing_fields=["Your profile has no name."]),
            PauseKind.MISSING_INFORMATION,
            "missing",
        ),
        (
            Stage.VERIFY,
            replace(AFTER[Stage.PREPARE], unverified=["Resume: “Led 40 people”"]),
            PauseKind.VERIFICATION_FAILED,
            "aren't verified",
        ),
        (
            Stage.VERIFY,
            replace(AFTER[Stage.PREPARE], unanswered=["Why us?"]),
            PauseKind.AMBIGUOUS_FIELDS,
            "no answer yet",
        ),
        (
            Stage.VERIFY,
            replace(AFTER[Stage.PREPARE], ambiguous=["What is your expected salary?"]),
            PauseKind.AMBIGUOUS_FIELDS,
            "can't answer from your evidence",
        ),
        (Stage.REVIEW, AFTER[Stage.VERIFY], PauseKind.APPROVAL_REQUIRED, "ready for your review"),
        (
            Stage.REVIEW,
            replace(AFTER[Stage.REVIEW], unapproved_answers=["Why us?"]),
            PauseKind.APPROVAL_REQUIRED,
            "Approve each application answer",
        ),
        (Stage.APPROVE, AFTER[Stage.REVIEW], PauseKind.APPROVAL_REQUIRED, "never approves"),
        (
            Stage.APPROVE,
            replace(AFTER[Stage.APPROVE], approval_current=False),
            PauseKind.APPROVAL_REQUIRED,
            "approve it",
        ),
        (Stage.SUBMIT, AFTER[Stage.APPROVE], PauseKind.APPROVAL_REQUIRED, "never submits"),
        (Stage.TRACK, AFTER[Stage.SUBMIT], PauseKind.MISSING_INFORMATION, "follow-up"),
    ],
)
def test_the_agent_stops_for_a_human_instead_of_moving_on(
    stage: Stage, facts: Facts, kind: PauseKind, phrase: str
) -> None:
    decision = decide(stage, facts)
    assert decision.next_stage is None and decision.pause is not None
    assert decision.pause.kind == kind and phrase in decision.pause.message


def test_uncertain_eligibility_needs_the_candidates_confirmation() -> None:
    uncertain = replace(AFTER[Stage.MATCH], disqualifying=["Fluent Japanese"])
    pause = exit_check(Stage.MATCH, uncertain)
    assert pause is not None and pause.items == ["May disqualify you: Fluent Japanese"]
    confirmed = replace(uncertain, eligibility_confirmed=True)
    assert decide(Stage.MATCH, confirmed).next_stage == Stage.PREPARE
    late = replace(AFTER[Stage.ANALYZE], deadline_passed=True, eligibility_confirmed=True)
    assert decide(Stage.ANALYZE, late).next_stage == Stage.MATCH


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Stage.DISCOVER, Stage.MATCH),  # skipping a stage
        (Stage.MATCH, Stage.ANALYZE),  # going back
        (Stage.REVIEW, Stage.SUBMIT),  # skipping approval
        (Stage.APPROVE, Stage.TRACK),  # skipping submission
        (Stage.DISCOVER, Stage.DONE),
    ],
)
def test_transitions_out_of_order_are_refused(current: Stage, target: Stage) -> None:
    with pytest.raises(TransitionError, match="stages run in order"):
        validate_transition(current, target, AFTER[Stage.TRACK])


def test_nothing_follows_done() -> None:
    with pytest.raises(TransitionError, match="complete"):
        validate_transition(Stage.DONE, Stage.DISCOVER, AFTER[Stage.TRACK])


def test_a_stage_cannot_be_left_incomplete() -> None:
    with pytest.raises(TransitionError, match="isn't complete"):
        validate_transition(Stage.APPROVE, Stage.SUBMIT, AFTER[Stage.REVIEW])


@pytest.mark.parametrize(
    ("stage", "facts", "reason"),
    [
        (Stage.ANALYZE, Facts(), "no job selected"),
        (Stage.MATCH, Facts(job_id=JOB), "no analyzed requirements"),
        (Stage.PREPARE, AFTER[Stage.ANALYZE], "no current match"),
        (Stage.VERIFY, AFTER[Stage.MATCH], "no prepared application"),
        (Stage.REVIEW, replace(AFTER[Stage.VERIFY], unverified=["x"]), "aren't verified"),
        (Stage.REVIEW, replace(AFTER[Stage.VERIFY], ambiguous=["Salary?"]), "aren't verified"),
        (Stage.APPROVE, AFTER[Stage.VERIFY], "isn't under review"),
        (Stage.SUBMIT, AFTER[Stage.REVIEW], "isn't approved"),
        (Stage.TRACK, AFTER[Stage.APPROVE], "isn't submitted"),
        (Stage.DONE, AFTER[Stage.SUBMIT], "nothing is being tracked"),
    ],
)
def test_each_stage_checks_its_preconditions(stage: Stage, facts: Facts, reason: str) -> None:
    assert reason in (entry_check(stage, facts) or "")


def test_an_approval_that_went_stale_cannot_be_submitted() -> None:
    stale = replace(AFTER[Stage.APPROVE], approval_current=False)
    assert entry_check(Stage.SUBMIT, stale) == "the application isn't approved"


# --- Tools ------------------------------------------------------------------------------------


def test_every_agent_has_explicit_tools_and_every_stage_is_covered() -> None:
    agents = {t.agent for t in TOOLS}
    assert agents == set(AgentName) - {AgentName.ORCHESTRATOR}  # all ten agents
    assert all(BY_STAGE[s] for s in ORDER if s != Stage.DONE)
    assert BY_STAGE[Stage.DONE] == []
    assert len({t.name for t in TOOLS}) == len(TOOLS)


def test_no_tool_approves_or_submits() -> None:
    names = {t.name for t in TOOLS}
    assert not {n for n in names if n.startswith(("approve", "submit"))}
    approval_tools = {t.name for t in TOOLS if t.agent == AgentName.HUMAN_APPROVAL}
    assert approval_tools == {"request_review", "check_approval", "check_submission"}


# --- Redaction --------------------------------------------------------------------------------


def test_configured_secrets_are_never_logged() -> None:
    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("my-configured-anthropic-key-123"),
        voyage_api_key=SecretStr("my-configured-voyage-key-456"),
        database_url=SecretStr("postgresql+asyncpg://app:db-pass-789@db.internal/careerpilot"),
    )
    secrets = secrets_of(settings)
    assert {
        "my-configured-anthropic-key-123",
        "my-configured-voyage-key-456",
        "db-pass-789",
    } <= set(secrets)
    text = (
        "calling with my-configured-anthropic-key-123 and my-configured-voyage-key-456 "
        "on postgresql+asyncpg://app:db-pass-789@db.internal/careerpilot"
    )
    cleaned = redact(text, secrets)
    for secret in ("anthropic-key-123", "voyage-key-456", "db-pass-789"):
        assert secret not in cleaned
    assert cleaned.count(REDACTED) == 3


@pytest.mark.parametrize(
    "leak",
    [
        "x-api-key: sk-ant-api03-AbCdEf0123456789",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        'payload {"api_key": "abc123def456"}',
        "token=ghp_0123456789abcdef",
        "password: hunter2hunter2",
        "OPENAI key sk-proj0123456789abcdefghij",
        "postgresql://user:p4ssw0rd@localhost/db",
    ],
)
def test_key_shaped_values_are_redacted(leak: str) -> None:
    cleaned = redact(leak)
    assert REDACTED in cleaned
    for fragment in (
        "AbCdEf0123",
        "payload.sig",
        "abc123def456",
        "0123456789abcdef",
        "hunter2",
        "proj0123456789",
        "p4ssw0rd",
    ):
        assert fragment not in cleaned


def test_summaries_are_truncated() -> None:
    assert len(redact("x" * 2000)) == 500
