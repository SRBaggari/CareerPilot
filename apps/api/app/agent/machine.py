"""The agent's state machine: pure, deterministic, and independent of the database.

DISCOVER → ANALYZE → MATCH → PREPARE → VERIFY → REVIEW → APPROVE → SUBMIT → TRACK → DONE

Every transition is validated twice:

- the **exit check** of the current stage: has it achieved what it must? If not, the agent
  stops and asks for human input (a ``Pause``) instead of guessing or retrying;
- the **entry check** of the next stage: are its preconditions met? A transition that skips
  a stage, goes backwards, or lacks its preconditions raises ``TransitionError``.

Both checks read ``Facts``: a snapshot of the real state (profile, job, match, documents,
application, approval), gathered fresh from the database before every decision. The
machine never trusts its own record of what happened.
"""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise


class Stage(StrEnum):
    DISCOVER = "discover"
    ANALYZE = "analyze"
    MATCH = "match"
    PREPARE = "prepare"
    VERIFY = "verify"
    REVIEW = "review"
    APPROVE = "approve"
    SUBMIT = "submit"
    TRACK = "track"
    DONE = "done"


ORDER: tuple[Stage, ...] = tuple(Stage)
NEXT: dict[Stage, Stage] = dict(pairwise(ORDER))


class PauseKind(StrEnum):
    """Why the agent stopped and needs a human."""

    MISSING_INFORMATION = "missing_information"
    ELIGIBILITY_UNCERTAIN = "eligibility_uncertain"
    VERIFICATION_FAILED = "verification_failed"
    AMBIGUOUS_FIELDS = "ambiguous_fields"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True)
class Pause:
    kind: PauseKind
    message: str
    items: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Facts:
    """The real state the machine decides from."""

    profile_missing: list[str] = field(default_factory=list)  # what the profile lacks
    job_id: uuid.UUID | None = None
    requirements: int = 0  # assessable requirements in the job analysis
    deadline_passed: bool = False
    matched: bool = False  # a current (not stale) match report exists
    disqualifying: list[str] = field(default_factory=list)  # required, and missing
    unknown_required: list[str] = field(default_factory=list)  # required, can't be assessed
    eligibility_confirmed: bool = False  # the candidate confirmed they want to proceed
    application_id: uuid.UUID | None = None
    resume_attached: bool = False
    missing_fields: list[str] = field(default_factory=list)  # required fields with no value
    unverified: list[str] = field(default_factory=list)  # claims not verified
    unanswered: list[str] = field(default_factory=list)  # questions without an answer
    # Questions CareerPilot can't map to the candidate's evidence (e.g. salary, notice
    # period), whose generated answer the candidate hasn't approved.
    ambiguous: list[str] = field(default_factory=list)
    review_state: str | None = None  # the application's approval state
    unapproved_answers: list[str] = field(default_factory=list)
    approval_current: bool = False  # approved, and unchanged since
    submitted: bool = False
    tracked: bool = False  # a follow-up is scheduled


class TransitionError(Exception):
    """A transition that the state machine doesn't allow."""


REVIEWING = ("ready_for_review", "approved", "submitted")
APPROVED = ("approved", "submitted")


def exit_check(stage: Stage, f: Facts) -> Pause | None:
    """What stops the agent from leaving ``stage``, or None when the stage is complete."""
    if stage == Stage.DISCOVER:
        if f.profile_missing:
            return Pause(
                PauseKind.MISSING_INFORMATION,
                "Your profile is missing information the application needs.",
                f.profile_missing,
            )
        if f.job_id is None:
            return Pause(PauseKind.MISSING_INFORMATION, "Choose a job to apply for.")
    elif stage == Stage.ANALYZE:
        if f.requirements == 0:
            return Pause(
                PauseKind.MISSING_INFORMATION,
                "The job analysis found no requirements to match your evidence against. "
                "Add the full job description, then continue.",
            )
        if f.deadline_passed and not f.eligibility_confirmed:
            return Pause(
                PauseKind.ELIGIBILITY_UNCERTAIN,
                "The application deadline has passed. Confirm you still want to apply.",
            )
    elif stage == Stage.MATCH:
        if not f.matched:
            return Pause(PauseKind.MISSING_INFORMATION, "The match couldn't be computed.")
        if (f.disqualifying or f.unknown_required) and not f.eligibility_confirmed:
            return Pause(
                PauseKind.ELIGIBILITY_UNCERTAIN,
                "Your evidence doesn't show some required qualifications, so eligibility is "
                "uncertain. Confirm you meet them (or want to apply anyway) to continue.",
                [f"May disqualify you: {r}" for r in f.disqualifying]
                + [
                    f"Can't tell from your profile: {r}"
                    for r in f.unknown_required
                    if r not in f.disqualifying
                ],
            )
    elif stage == Stage.PREPARE:
        if f.application_id is None or not f.resume_attached:
            return Pause(
                PauseKind.MISSING_INFORMATION,
                "The application couldn't be prepared: it needs a tailored resume.",
            )
    elif stage == Stage.VERIFY:
        if f.missing_fields:
            return Pause(
                PauseKind.MISSING_INFORMATION,
                "Required information is missing.",
                f.missing_fields,
            )
        if f.unverified:
            return Pause(
                PauseKind.VERIFICATION_FAILED,
                "Some claims aren't verified against your evidence. Edit, re-verify or "
                "regenerate the documents, then continue.",
                f.unverified,
            )
        if f.unanswered or f.ambiguous:
            return Pause(
                PauseKind.AMBIGUOUS_FIELDS,
                "Some application questions ask for something CareerPilot can't answer from "
                "your evidence (or have no answer yet). Write or edit the answer and approve "
                "it, or delete the question, then continue.",
                f.unanswered + f.ambiguous,
            )
    elif stage == Stage.REVIEW:
        if f.review_state not in REVIEWING:
            return Pause(PauseKind.APPROVAL_REQUIRED, "Mark the application ready for your review.")
        if f.unapproved_answers:
            return Pause(
                PauseKind.APPROVAL_REQUIRED,
                "Approve each application answer.",
                f.unapproved_answers,
            )
    elif stage == Stage.APPROVE:
        if f.review_state not in APPROVED or not f.approval_current:
            return Pause(
                PauseKind.APPROVAL_REQUIRED,
                "Review the application and approve it. CareerPilot never approves for you.",
            )
    elif stage == Stage.SUBMIT:
        if not f.submitted:
            return Pause(
                PauseKind.APPROVAL_REQUIRED,
                "Submit the application yourself and record it, or use browser assistance "
                "(which submits only after your confirmation). CareerPilot never submits on "
                "its own.",
            )
    elif stage == Stage.TRACK:
        if not f.tracked:
            return Pause(PauseKind.MISSING_INFORMATION, "Schedule a follow-up reminder.")
    return None


def entry_check(stage: Stage, f: Facts) -> str | None:
    """Why ``stage`` can't be entered, or None when its preconditions hold."""
    requirements: dict[Stage, tuple[bool, str]] = {
        Stage.ANALYZE: (f.job_id is not None, "no job selected"),
        Stage.MATCH: (f.requirements > 0, "the job has no analyzed requirements"),
        Stage.PREPARE: (f.matched, "there is no current match"),
        Stage.VERIFY: (
            f.application_id is not None and f.resume_attached,
            "there is no prepared application with a resume",
        ),
        Stage.REVIEW: (
            not f.unverified and not f.missing_fields and not f.unanswered and not f.ambiguous,
            "the documents aren't verified",
        ),
        Stage.APPROVE: (f.review_state in REVIEWING, "the application isn't under review"),
        Stage.SUBMIT: (
            f.review_state in APPROVED and f.approval_current,
            "the application isn't approved",
        ),
        Stage.TRACK: (f.submitted, "the application isn't submitted"),
        Stage.DONE: (f.tracked, "nothing is being tracked"),
    }
    ok, reason = requirements.get(stage, (True, ""))
    return None if ok else reason


def validate_transition(current: Stage, target: Stage, f: Facts) -> None:
    """Raise ``TransitionError`` unless ``current → target`` is allowed right now."""
    if current == Stage.DONE:
        raise TransitionError("The run is complete.")
    if NEXT[current] != target:
        raise TransitionError(
            f"Can't go from {current.value} to {target.value}: stages run in order "
            f"({current.value} → {NEXT[current].value})."
        )
    pause = exit_check(current, f)
    if pause is not None:
        raise TransitionError(f"{current.value} isn't complete: {pause.message}")
    reason = entry_check(target, f)
    if reason is not None:
        raise TransitionError(f"Can't enter {target.value}: {reason}.")


@dataclass(frozen=True)
class Decision:
    next_stage: Stage | None  # where to go, or None to stay
    pause: Pause | None  # why the agent stopped, when it stays


def decide(stage: Stage, f: Facts) -> Decision:
    """After a stage's tools ran: move on (validated), or stop and ask a human."""
    pause = exit_check(stage, f)
    if pause is not None:
        return Decision(None, pause)
    target = NEXT[stage]
    validate_transition(stage, target, f)
    return Decision(target, None)
