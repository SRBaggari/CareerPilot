import { apiFetch, jsonBody } from "./client";

export type Stage =
  | "discover"
  | "analyze"
  | "match"
  | "prepare"
  | "verify"
  | "review"
  | "approve"
  | "submit"
  | "track"
  | "done";

export type RunStatus =
  "ready" | "running" | "waiting_for_human" | "completed" | "failed" | "cancelled";

export type PauseKind =
  | "missing_information"
  | "eligibility_uncertain"
  | "verification_failed"
  | "ambiguous_fields"
  | "approval_required";

export const STAGE_LABELS: Record<Stage, string> = {
  discover: "Discover",
  analyze: "Analyze",
  match: "Match",
  prepare: "Prepare",
  verify: "Verify",
  review: "Review",
  approve: "Approve",
  submit: "Submit",
  track: "Track",
  done: "Done",
};

export const STATUS_LABELS: Record<RunStatus, string> = {
  ready: "Ready to continue",
  running: "Working…",
  waiting_for_human: "Waiting for you",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const PAUSE_LABELS: Record<PauseKind, string> = {
  missing_information: "Information is missing",
  eligibility_uncertain: "Eligibility is uncertain",
  verification_failed: "Claim verification failed",
  ambiguous_fields: "Some application fields are ambiguous",
  approval_required: "Your approval is required",
};

export type AgentAction = {
  id: string;
  at: string;
  agent: string;
  stage: Stage;
  task: string;
  tool: string;
  input_summary: string;
  output_summary: string | null;
  status: "succeeded" | "skipped" | "paused" | "failed";
  error: string | null;
  duration_ms: number;
};

export type AgentRun = {
  id: string;
  stage: Stage;
  status: RunStatus;
  pause: { kind: PauseKind; message: string; items: string[] } | null;
  goal: {
    job_id?: string;
    source?: string;
    external_id?: string;
    include_cover_letter: boolean;
    questions: string[];
  };
  inputs: Record<string, unknown>;
  job_id: string | null;
  application_id: string | null;
  steps: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  stages: { stage: Stage; state: "done" | "current" | "pending" }[];
  log: AgentAction[];
};

const ROOT = "/api/v1/agent/runs";

export const listRuns = () => apiFetch<AgentRun[]>(ROOT);
export const getRun = (id: string) => apiFetch<AgentRun>(`${ROOT}/${id}`);
export const startRun = (body: {
  job_id: string;
  include_cover_letter: boolean;
  questions: string[];
}) => apiFetch<AgentRun>(ROOT, { method: "POST", body: jsonBody(body) });
/** Runs stages until the agent needs you; it never approves or submits. */
export const advanceRun = (id: string, body: { confirm_eligibility?: boolean } = {}) =>
  apiFetch<AgentRun>(`${ROOT}/${id}/advance`, { method: "POST", body: jsonBody(body) });
export const cancelRun = (id: string) =>
  apiFetch<AgentRun>(`${ROOT}/${id}/cancel`, { method: "POST" });
