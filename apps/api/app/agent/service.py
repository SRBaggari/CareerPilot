"""The orchestrator: runs the agents' tools stage by stage, under the state machine.

There is no autonomous loop. Each ``advance`` request runs at most ``max_stages`` stages
(at most one pass through the machine), and a run may make at most
``MAX_TOOL_CALLS_PER_RUN`` tool calls in total. The agent stops as soon as a stage needs a
human (missing information, uncertain eligibility, failed verification, ambiguous fields,
approval or submission), and continues only when the candidate asks it to.

Every tool call, transition, pause and error is written to the agent execution log, with
inputs, outputs and errors redacted so no secret is ever stored.
"""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.machine import ORDER, Pause, PauseKind, Stage, TransitionError, decide
from app.agent.models import ActionStatus, AgentActionLog, AgentName, AgentRun, RunStatus
from app.agent.redact import redact, secrets_of
from app.agent.tools import BY_STAGE, TOOLS, Ctx, Deps, Tool, gather
from app.core.errors import ConflictError, DomainError, NotFoundError
from app.profiles import service as profiles
from app.users.models import User

logger = logging.getLogger(__name__)
MAX_STAGES_PER_ADVANCE = len(ORDER) - 1
MAX_TOOL_CALLS_PER_RUN = 60
OPEN = (RunStatus.READY, RunStatus.WAITING_FOR_HUMAN, RunStatus.FAILED)
# A run left 'running' this long was interrupted (a crash or a dropped request), so it may
# be advanced again. Every tool call commits, which refreshes ``updated_at``.
RUNNING_STALE_AFTER = timedelta(minutes=15)


# --- Schemas ----------------------------------------------------------------------------------


class RunStart(BaseModel):
    """What to apply for: a job you have, or a posting from a discovery source."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID | None = None
    source: str | None = Field(default=None, max_length=50)
    external_id: str | None = Field(default=None, max_length=200)
    include_cover_letter: bool = True
    questions: list[Annotated[str, Field(max_length=1000)]] = Field(
        default_factory=list, max_length=10
    )

    @model_validator(mode="after")
    def _one_target(self) -> "RunStart":
        if (self.job_id is None) == (self.source is None or self.external_id is None):
            raise ValueError("Give either job_id, or source and external_id.")
        self.questions = [q.strip() for q in self.questions if q.strip()]
        return self


class AdvanceIn(BaseModel):
    """Continue the run. Only decisions the candidate alone can make are accepted."""

    model_config = ConfigDict(extra="forbid")

    confirm_eligibility: bool | None = None  # proceed despite uncertain eligibility
    max_stages: int = Field(default=MAX_STAGES_PER_ADVANCE, ge=1, le=MAX_STAGES_PER_ADVANCE)


class ActionOut(BaseModel):
    id: uuid.UUID
    at: datetime
    agent: AgentName
    stage: Stage
    task: str
    tool: str
    input_summary: str
    output_summary: str | None
    status: ActionStatus
    error: str | None
    duration_ms: int


class StageOut(BaseModel):
    stage: Stage
    state: str  # done, current, pending


class RunOut(BaseModel):
    id: uuid.UUID
    stage: Stage
    status: RunStatus
    pause: dict[str, Any] | None
    goal: dict[str, Any]
    inputs: dict[str, Any]
    job_id: uuid.UUID | None
    application_id: uuid.UUID | None
    steps: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    stages: list[StageOut]
    log: list[ActionOut]


class ToolOut(BaseModel):
    name: str
    agent: AgentName
    stage: Stage
    description: str


# --- Logging ----------------------------------------------------------------------------------


class _Log:
    """Writes redacted, strictly ordered rows to the execution log."""

    def __init__(self, session: AsyncSession, run: AgentRun, secrets: list[str]) -> None:
        self.session, self.run, self.secrets = session, run, secrets
        self.last: datetime | None = None

    async def write(
        self,
        *,
        agent: AgentName,
        stage: Stage,
        task: str,
        tool: str,
        inputs: str,
        output: str | None,
        status: ActionStatus,
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        if self.last is None:
            self.last = await self.session.scalar(
                select(func.max(AgentActionLog.created_at)).where(
                    AgentActionLog.run_id == self.run.id
                )
            )
        at = datetime.now(UTC)
        if self.last is not None and at <= self.last:
            at = self.last + timedelta(microseconds=1)
        self.last = at
        self.session.add(
            AgentActionLog(
                run_id=self.run.id,
                agent=agent,
                stage=stage,
                task=redact(task, self.secrets, 300),
                tool=tool,
                input_summary=redact(inputs, self.secrets),
                output_summary=redact(output, self.secrets) if output is not None else None,
                status=status,
                error=redact(error, self.secrets) if error is not None else None,
                duration_ms=max(duration_ms, 0),
                created_at=at,
            )
        )
        await self.session.flush()


# --- Queries ----------------------------------------------------------------------------------


async def _owned(
    session: AsyncSession, user: User, run_id: uuid.UUID, *, lock: bool = False
) -> AgentRun:
    profile = await profiles.get_profile(session, user)
    statement = (
        select(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.candidate_profile_id == profile.id)
        .execution_options(populate_existing=True)
    )
    run = await session.scalar(statement.with_for_update() if lock else statement)
    if run is None:
        raise NotFoundError("Agent run not found.")
    return run


async def _out(session: AsyncSession, run: AgentRun) -> RunOut:
    rows = await session.scalars(
        select(AgentActionLog)
        .where(AgentActionLog.run_id == run.id)
        .order_by(AgentActionLog.created_at, AgentActionLog.id)
    )
    position = ORDER.index(run.stage)
    return RunOut(
        id=run.id,
        stage=run.stage,
        status=run.status,
        pause=run.pause,
        goal=run.goal,
        inputs=run.inputs,
        job_id=run.job_id,
        application_id=run.application_id,
        steps=run.steps,
        last_error=run.last_error,
        created_at=run.created_at,
        updated_at=run.updated_at,
        stages=[
            StageOut(
                stage=s,
                state="done"
                if n < position or run.stage == Stage.DONE
                else "current"
                if n == position
                else "pending",
            )
            for n, s in enumerate(ORDER)
            if s != Stage.DONE
        ],
        log=[
            ActionOut(
                id=r.id,
                at=r.created_at,
                agent=r.agent,
                stage=r.stage,
                task=r.task,
                tool=r.tool,
                input_summary=r.input_summary,
                output_summary=r.output_summary,
                status=r.status,
                error=r.error,
                duration_ms=r.duration_ms,
            )
            for r in rows
        ],
    )


def tools() -> list[ToolOut]:
    return [
        ToolOut(name=t.name, agent=t.agent, stage=t.stage, description=t.description) for t in TOOLS
    ]


async def list_runs(session: AsyncSession, user: User) -> list[RunOut]:
    profile = await profiles.get_profile(session, user)
    runs = await session.scalars(
        select(AgentRun)
        .where(AgentRun.candidate_profile_id == profile.id)
        .order_by(AgentRun.created_at.desc())
        .limit(50)
    )
    return [await _out(session, r) for r in runs]


async def get(session: AsyncSession, user: User, run_id: uuid.UUID) -> RunOut:
    return await _out(session, await _owned(session, user, run_id))


# --- Commands ---------------------------------------------------------------------------------


async def start(session: AsyncSession, user: User, payload: RunStart, deps: Deps) -> RunOut:
    profile = await profiles.get_profile(session, user)
    goal = payload.model_dump(mode="json", exclude={"max_stages"}, exclude_none=True)
    run = AgentRun(candidate_profile_id=profile.id, goal=goal, inputs={})
    session.add(run)
    await session.flush()
    target = (
        f"job {payload.job_id}"
        if payload.job_id
        else f"posting {payload.source}:{payload.external_id}"
    )
    await _Log(session, run, secrets_of(deps.settings)).write(
        agent=AgentName.ORCHESTRATOR,
        stage=Stage.DISCOVER,
        task="Start an application run",
        tool="start",
        inputs=f"{target}; cover letter: {payload.include_cover_letter}; "
        f"{len(payload.questions)} questions",
        output="Run created at the discover stage. Nothing runs until you advance it.",
        status=ActionStatus.SUCCEEDED,
    )
    await session.commit()
    return await get(session, user, run.id)


async def cancel(session: AsyncSession, user: User, run_id: uuid.UUID, deps: Deps) -> RunOut:
    run = await _owned(session, user, run_id, lock=True)
    if run.status == RunStatus.RUNNING and not _stale(run):
        raise ConflictError("This run is working. Cancel it once it stops.")
    if run.status not in (*OPEN, RunStatus.RUNNING):
        raise ConflictError("Only an open run can be cancelled.")
    run.status, run.pause = RunStatus.CANCELLED, None
    await _Log(session, run, secrets_of(deps.settings)).write(
        agent=AgentName.ORCHESTRATOR,
        stage=run.stage,
        task="Cancel the run",
        tool="cancel",
        inputs="requested by you",
        output="Cancelled. Nothing already prepared was deleted.",
        status=ActionStatus.SUCCEEDED,
    )
    await session.commit()
    return await get(session, user, run.id)


def _pause_dict(pause: Pause, secrets: list[str]) -> dict[str, Any]:
    return {
        "kind": pause.kind.value,
        "message": redact(pause.message, secrets),
        "items": [redact(i, secrets) for i in pause.items],
    }


async def _call(ctx: Ctx, log: _Log, tool: Tool) -> None:
    """Run one tool and log it. Domain errors propagate (the stage pauses)."""
    started = time.monotonic()
    inputs = tool.inputs(ctx)
    ctx.run.steps += 1
    try:
        result = await tool.run(ctx)
    except DomainError as exc:
        await log.write(
            agent=tool.agent,
            stage=tool.stage,
            task=tool.description,
            tool=tool.name,
            inputs=inputs,
            output=None,
            status=ActionStatus.FAILED,
            error=exc.message,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    await log.write(
        agent=tool.agent,
        stage=tool.stage,
        task=tool.description,
        tool=tool.name,
        inputs=inputs,
        output=result.output,
        status=result.status,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    await ctx.session.commit()


async def advance(
    session: AsyncSession, user: User, run_id: uuid.UUID, payload: AdvanceIn, deps: Deps
) -> RunOut:
    """Run stages until the agent needs a human, completes, fails, or reaches the limit."""
    run = await _owned(session, user, run_id, lock=True)
    if run.status == RunStatus.RUNNING and not _stale(run):
        raise ConflictError("This run is already working. Wait for it to stop, then refresh.")
    if run.status not in (*OPEN, RunStatus.RUNNING):
        raise ConflictError(f"This run is {run.status.value}; start a new one.")
    secrets = secrets_of(deps.settings)
    log = _Log(session, run, secrets)
    if payload.confirm_eligibility is not None:
        run.inputs = {**run.inputs, "confirm_eligibility": payload.confirm_eligibility}
        await log.write(
            agent=AgentName.ORCHESTRATOR,
            stage=run.stage,
            task="Record your decision",
            tool="human_input",
            inputs=f"confirm_eligibility={payload.confirm_eligibility}",
            output="You confirmed you want to proceed despite uncertain eligibility."
            if payload.confirm_eligibility
            else "You did not confirm eligibility.",
            status=ActionStatus.SUCCEEDED,
        )
    # Claimed under the row lock and committed: a concurrent advance now sees RUNNING and is
    # refused instead of running the same tools a second time.
    run.status, run.pause, run.last_error = RunStatus.RUNNING, None, None
    await session.commit()
    ctx = Ctx(session=session, user=user, run=run, deps=deps)

    for _ in range(payload.max_stages):
        stage = run.stage
        pending = BY_STAGE[stage]
        if run.steps + len(pending) > MAX_TOOL_CALLS_PER_RUN:
            await _fail(session, user, run_id, log, stage, "The run reached its step limit.")
            break
        try:
            for tool in pending:
                await _call(ctx, log, tool)
            facts = await gather(ctx)
            decision = decide(stage, facts)
        except DomainError as exc:
            # The service refused (e.g. nothing to tailor from): a human must act. Its
            # failed call is already logged; keep it.
            await _pause(log, run, stage, Pause(PauseKind.MISSING_INFORMATION, exc.message))
            await session.commit()
            break
        except TransitionError as exc:
            await _fail(session, user, run_id, log, stage, str(exc))
            break
        except Exception as exc:
            # Details (which may include SQL or internal paths) go to the server log only.
            logger.exception("Agent run %s failed at %s", run_id, stage.value)
            message = f"{type(exc).__name__}: an unexpected error stopped this stage."
            await _fail(session, user, run_id, log, stage, message, rollback=True)
            break
        if decision.pause is not None:
            await _pause(log, run, stage, decision.pause)
            await session.commit()
            break
        assert decision.next_stage is not None  # noqa: S101 - decide() moved on
        await log.write(
            agent=AgentName.ORCHESTRATOR,
            stage=stage,
            task=f"Move from {stage.value} to {decision.next_stage.value}",
            tool="transition",
            inputs=f"{stage.value} → {decision.next_stage.value}",
            output=f"Validated: {stage.value} is complete and {decision.next_stage.value}'s "
            "preconditions hold.",
            status=ActionStatus.SUCCEEDED,
        )
        run.stage = decision.next_stage
        if run.stage == Stage.DONE:
            run.status = RunStatus.COMPLETED
            await log.write(
                agent=AgentName.ORCHESTRATOR,
                stage=Stage.DONE,
                task="Finish the run",
                tool="complete",
                inputs="",
                output="The application is submitted and being tracked.",
                status=ActionStatus.SUCCEEDED,
            )
        await session.commit()
        if run.stage == Stage.DONE:
            break
    if run.status == RunStatus.RUNNING:  # stopped at max_stages: ready for the next advance
        run.status = RunStatus.READY
        await session.commit()
    return await get(session, user, run_id)


def _stale(run: AgentRun) -> bool:
    return run.updated_at < datetime.now(UTC) - RUNNING_STALE_AFTER


async def _pause(log: _Log, run: AgentRun, stage: Stage, pause: Pause) -> None:
    run.status, run.pause = RunStatus.WAITING_FOR_HUMAN, _pause_dict(pause, log.secrets)
    await log.write(
        agent=AgentName.ORCHESTRATOR,
        stage=stage,
        task=f"Stop at {stage.value} and ask for your input",
        tool="pause",
        inputs=pause.kind.value,
        output=pause.message + (" " + "; ".join(pause.items) if pause.items else ""),
        status=ActionStatus.PAUSED,
    )


async def _fail(
    session: AsyncSession,
    user: User,
    run_id: uuid.UUID,
    log: _Log,
    stage: Stage,
    error: str,
    *,
    rollback: bool = False,
) -> None:
    if rollback:
        await session.rollback()
        await session.refresh(user)  # the rollback expired it
    run = log.run = await _owned(session, user, run_id)
    log.last = None
    run.status, run.pause = RunStatus.FAILED, None
    run.last_error = redact(error, log.secrets)
    await log.write(
        agent=AgentName.ORCHESTRATOR,
        stage=stage,
        task=f"Run the {stage.value} stage",
        tool="error",
        inputs=stage.value,
        output=None,
        status=ActionStatus.FAILED,
        error=error,
    )
    await session.commit()


__all__ = ["AdvanceIn", "RunOut", "RunStart", "ToolOut"]
