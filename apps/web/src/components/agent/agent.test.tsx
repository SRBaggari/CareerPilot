import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentRun, Stage } from "@/lib/api/agent";

import { stubApi } from "../profile/testApi";
import { AgentPage } from "./AgentPage";
import { AgentRunPage } from "./AgentRunPage";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
afterEach(() => vi.unstubAllGlobals());

const ORDER: Stage[] = [
  "discover",
  "analyze",
  "match",
  "prepare",
  "verify",
  "review",
  "approve",
  "submit",
  "track",
];

function run(overrides: Partial<AgentRun> = {}): AgentRun {
  const stage = overrides.stage ?? "match";
  const at = ORDER.indexOf(stage);
  return {
    id: "r1",
    stage,
    status: "waiting_for_human",
    pause: {
      kind: "eligibility_uncertain",
      message: "Your evidence doesn't show some required qualifications.",
      items: ["May disqualify you: Must be authorized to work in the United States"],
    },
    goal: { job_id: "j1", include_cover_letter: true, questions: [] },
    inputs: {},
    job_id: "j1",
    application_id: null,
    steps: 4,
    last_error: null,
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    stages: ORDER.map((s, n) => ({
      stage: s,
      state: stage === "done" || n < at ? "done" : n === at ? "current" : "pending",
    })),
    log: [
      {
        id: "l1",
        at: "2026-10-01T09:00:01Z",
        agent: "matching",
        stage: "match",
        task: "Match the job's requirements to verified evidence.",
        tool: "compute_match",
        input_summary: "job j1",
        output_summary: "Evidence coverage 62%",
        status: "succeeded",
        error: null,
        duration_ms: 40,
      },
      {
        id: "l2",
        at: "2026-10-01T09:00:02Z",
        agent: "orchestrator",
        stage: "match",
        task: "Stop at match and ask for your input",
        tool: "pause",
        input_summary: "eligibility_uncertain",
        output_summary: "Your evidence doesn't show some required qualifications.",
        status: "paused",
        error: null,
        duration_ms: 0,
      },
    ],
    ...overrides,
  };
}

describe("AgentRunPage", () => {
  it("shows the stages, why it stopped, and the execution log", async () => {
    stubApi({ "GET /agent/runs/r1": () => ({ status: 200, body: run() }) });
    render(<AgentRunPage runId="r1" />);
    const stages = await screen.findByLabelText("Stages");
    expect(within(stages).getByText("Match")).toHaveAttribute("aria-current", "step");
    const pause = screen.getByRole("region", { name: "The agent needs you" });
    expect(pause).toHaveTextContent("Eligibility is uncertain");
    expect(pause).toHaveTextContent("Must be authorized to work in the United States");
    const log = screen.getByRole("table", { name: "Execution log" });
    expect(within(log).getAllByRole("row")).toHaveLength(3);
    expect(log).toHaveTextContent("compute_match");
    expect(log).toHaveTextContent("Evidence coverage 62%");
  });

  it("continues past uncertain eligibility only with the candidate's confirmation", async () => {
    const calls = stubApi({
      "GET /agent/runs/r1": () => ({ status: 200, body: run() }),
      "POST /agent/runs/r1/advance": () => ({
        status: 200,
        body: run({
          stage: "approve",
          application_id: "a1",
          pause: {
            kind: "approval_required",
            message: "Review the application and approve it. CareerPilot never approves for you.",
            items: [],
          },
        }),
      }),
    });
    render(<AgentRunPage runId="r1" />);
    const proceed = await screen.findByRole("button", { name: "Continue" });
    expect(proceed).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(proceed);
    expect(await screen.findByText(/never approves for you/)).toBeVisible();
    expect(calls.find((c) => c.key === "POST /agent/runs/r1/advance")?.body).toEqual({
      confirm_eligibility: true,
    });
    // Approval happens on the review page, by the candidate.
    expect(screen.getByRole("link", { name: "Review and approve" })).toHaveAttribute(
      "href",
      "/applications/a1/review",
    );
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
  });

  it("explains a failure and offers a retry", async () => {
    stubApi({
      "GET /agent/runs/r1": () => ({
        status: 200,
        body: run({ status: "failed", pause: null, last_error: "RuntimeError: [REDACTED]" }),
      }),
    });
    render(<AgentRunPage runId="r1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("RuntimeError: [REDACTED]");
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("offers nothing to do once completed", async () => {
    stubApi({
      "GET /agent/runs/r1": () => ({
        status: 200,
        body: run({ stage: "done", status: "completed", pause: null, application_id: "a1" }),
      }),
    });
    render(<AgentRunPage runId="r1" />);
    expect(await screen.findByText(/submitted and being tracked/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  });

  it("checks back on a run another request is advancing until it stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let reads = 0;
    stubApi({
      "GET /agent/runs/r1": () => {
        reads += 1;
        return {
          status: 200,
          body: reads < 2 ? run({ status: "running", pause: null }) : run(),
        };
      },
    });
    render(<AgentRunPage runId="r1" />);
    expect(await screen.findByText("Working…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    await vi.advanceTimersByTimeAsync(3000);
    expect(await screen.findByText("Waiting for you")).toBeVisible();
    await vi.advanceTimersByTimeAsync(6000);
    expect(reads).toBe(2); // stopped checking once the run stopped
    vi.useRealTimers();
  });
});

describe("AgentPage", () => {
  it("creates a run for a chosen job with its questions", async () => {
    const calls = stubApi({
      "GET /jobs": () => ({
        status: 200,
        body: [{ id: "j1", title: "ML Engineer", company_name: "Northwind" }],
      }),
      "GET /agent/runs": () => ({ status: 200, body: [] }),
      "POST /agent/runs": () => ({
        status: 201,
        body: run({ stage: "discover", status: "ready" }),
      }),
    });
    render(<AgentPage initialJobId="j1" />);
    expect(await screen.findByLabelText(/^Job/)).toHaveValue("j1");
    fireEvent.change(screen.getByLabelText(/Application questions/), {
      target: { value: "Why us?\n\n Describe your Python experience. " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create the run" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/agent/r1"));
    expect(calls.find((c) => c.key === "POST /agent/runs")?.body).toEqual({
      job_id: "j1",
      include_cover_letter: true,
      questions: ["Why us?", "Describe your Python experience."],
    });
  });
});
