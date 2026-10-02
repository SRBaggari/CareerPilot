# The CareerPilot agent

The agent orchestrates CareerPilot's capabilities to take one job from discovery to a
tracked application, through an explicit, validated state machine. It is not an
autonomous loop: it only acts when the candidate asks it to continue, it stops whenever a
human is needed, and it never approves or submits anything.

UI: **Agent** (`/agent`), or **Apply with the agent** on a job's page.

## The state machine

```
DISCOVER → ANALYZE → MATCH → PREPARE → VERIFY → REVIEW → APPROVE → SUBMIT → TRACK → DONE
```

Each stage runs its tools, then the orchestrator gathers **facts** fresh from the database
(it never trusts its own record of what happened) and decides:

- **Exit check**: has the stage achieved what it must? If not, the agent **stops and asks
  for human input**.
- **Entry check** of the next stage: are its preconditions met?
- The transition must be to the **next** stage. Skipping (e.g. REVIEW → SUBMIT), going
  back, or leaving DONE raises `TransitionError` and fails the run.

| Stage | Tools (agent) | Leaves when | Otherwise stops with |
| --- | --- | --- | --- |
| DISCOVER | `check_profile` (Candidate Profile), `select_job` (Job Discovery: the chosen job, or imports a posting from a discovery source) | the profile has a name and confirmed evidence, and a job is selected | missing information |
| ANALYZE | `review_analysis` (Job Analysis) | the job has analyzed requirements and its deadline hasn't passed (or you confirmed) | missing information, eligibility uncertain |
| MATCH | `compute_match` (Matching; reuses a current match) | the match is current, and nothing may disqualify you (or you confirmed) | eligibility uncertain |
| PREPARE | `tailor_resume` (Resume), `write_cover_letter` (Cover Letter), `prepare_application` (Application Preparation: tracks it, attaches documents, answers the questions, marks it prepared) | an application with a resume exists | missing information |
| VERIFY | `check_claims` (Claim Verification) | every document and answer is verified, nothing required is missing, every question has an answer CareerPilot can stand behind | verification failed, missing information, ambiguous fields |
| REVIEW | `request_review` (Human Approval) | the application is ready for review and every answer is approved by you | approval required |
| APPROVE | `check_approval` (Human Approval; withdraws a stale approval) | you approved exactly the current content | approval required |
| SUBMIT | `check_submission` (Human Approval) | you submitted it (yourself, or with browser assistance after your confirmation) | approval required |
| TRACK | `track_application` (Application Tracking: schedules a check-in) | a follow-up is scheduled | missing information |

The pure state machine is `app/agent/machine.py`; tools are explicit functions in
`app/agent/tools.py` (listed at `GET /api/v1/agent/tools`); the orchestrator is
`app/agent/service.py`.

### When the agent stops for a human

| Pause | Examples | What the candidate does |
| --- | --- | --- |
| `missing_information` | no confirmed evidence; no job; a service refused (e.g. the posting couldn't be fetched) | add it, then continue |
| `eligibility_uncertain` | a requirement that may disqualify (e.g. work authorization), a required qualification the profile can't show, a passed deadline | continue with `confirm_eligibility: true`, or cancel |
| `verification_failed` | a claim in the resume, cover letter or an answer isn't supported by evidence | edit, re-verify or regenerate, then continue |
| `ambiguous_fields` | a question with no answer, or one CareerPilot can't map to evidence (salary, notice period, relocation) whose answer you haven't approved | write and approve the answer, or delete the question |
| `approval_required` | answers to approve, the application to approve, the application to submit | act on the review page or the application, then continue |

Questions such as "What is your expected salary?" are classified as general questions:
the answer generator answers them with verified (but unrelated) evidence, so the agent
treats them as ambiguous until the candidate approves or removes them.

### Never autonomous

- `advance` runs at most `max_stages` stages (default and maximum: one pass through the
  machine) and stops at the first pause. Nothing runs in the background.
- A run makes at most 60 tool calls in total, then fails with "step limit".
- Tools are idempotent (they reuse a current match, the latest documents, an existing
  application), so continuing never duplicates work.
- No tool approves or submits. A test advances an approval-waiting run repeatedly and
  checks nothing was approved.
- A domain refusal from a service pauses the run (a human can act); an unexpected error
  fails it (continuing retries the stage).

## The execution log

`agent_action_logs` (append-only) records every tool call, transition, pause, human
input, completion and error:

| Field | Content |
| --- | --- |
| `task` | what the step does ("Match the job's requirements to verified evidence") |
| `tool` | the tool (or `transition`, `pause`, `human_input`, `start`, `cancel`, `complete`, `error`) |
| `agent` | which of the ten agents (or the orchestrator) |
| `stage` | the stage it ran in |
| `input_summary`, `output_summary` | short summaries (truncated to 500 characters) |
| `created_at` | timestamp, strictly increasing per run |
| `status` | succeeded, skipped, paused, failed |
| `error` | the error message, when it failed |
| `duration_ms` | how long it took |

Model calls made by the underlying services are also recorded in `ai_execution_logs`
(provider, model, tokens, latency) as before.

### No secrets in logs

Every summary, error, pause message and `last_error` passes through `redact`
(`app/agent/redact.py`) before it is stored:

- the configured secret values themselves (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, any
  setting named like a key, secret, token or password, and the database password);
- anything shaped like a key or credential: `sk-ant-…`, `sk-…`, `pa-…`, `Bearer …`,
  `api_key=…`, `token: …`, `password=…`, passwords in database URLs.

Tests inject the configured key into an exception and into a tool input and check that no
stored log row contains it.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/agent/tools` | The tools, by agent and stage |
| POST | `/api/v1/agent/runs` | `{job_id}` or `{source, external_id}`, plus `include_cover_letter`, `questions`. Creates the run; runs nothing |
| GET | `/api/v1/agent/runs`, `/api/v1/agent/runs/{id}` | Runs, with stages, pause and the execution log |
| POST | `/api/v1/agent/runs/{id}/advance` | `{confirm_eligibility?, max_stages?}`: continue until the agent needs you |
| POST | `/api/v1/agent/runs/{id}/cancel` | Cancel (nothing already prepared is deleted) |

Runs are private: another user gets 404, and a run can't use someone else's job.

## Database

| Table | Notes |
| --- | --- |
| `agent_runs` | `stage`, `status` (ready, running, waiting_for_human, completed, failed, cancelled; `running` is claimed under a row lock so only one request advances a run, and expires after 15 minutes if that request died), `goal`, `inputs` (the candidate's decisions), `pause`, `steps`, `last_error`, `job_id`, `application_id`. CHECKs: waiting ⇔ a pause is recorded; `done` ⇔ completed; steps ≥ 0 |
| `agent_action_logs` | The execution log above; indexed by (`run_id`, `created_at`) |

## Tests

- `tests/test_agent.py`: the state machine (order, every pause, eligibility confirmation,
  out-of-order and incomplete transitions refused, every entry precondition), the tool
  registry (all ten agents, every stage, no approving or submitting tool), redaction.
- `tests/integration/test_agent_api.py`: the full run on the real services with the human
  steps in between; the log; each kind of pause; bounded advancing; the step limit;
  failures and retry; secrets never stored; constraints; authorization.
