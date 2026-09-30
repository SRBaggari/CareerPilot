import { apiFetch, jsonBody } from "./client";

export type RunStatus =
  | "preparing"
  | "needs_input"
  | "awaiting_review"
  | "submitting"
  | "submitted"
  | "stopped"
  | "failed"
  | "cancelled";

export type ReviewFile = {
  field_label: string;
  document: "resume" | "cover_letter";
  version: number;
  file_name: string;
  size_bytes: number;
  sha256: string;
};

export type Review = {
  destination: { url: string; host: string; adapter: string; form_action: string };
  personal: {
    first_name: string;
    last_name: string;
    email: string;
    phone: string | null;
    location: string | null;
    linkedin: string | null;
  };
  resume: ReviewFile;
  cover_letter: ReviewFile | null;
  answers: { field_id: string; question: string; answer: string; answer_id: string }[];
  additional_fields: { field_id: string; label: string; value: string; source: string }[];
  left_blank: string[];
};

export type Problem = {
  field_id: string;
  label: string;
  kind: string;
  required: boolean;
  options: string[];
  message: string;
  needs_input: boolean;
};

export type AuditEvent = {
  id: string;
  at: string;
  actor: "user" | "system" | "automation";
  action: string;
  message: string;
  detail: Record<string, unknown>;
};

export type AssistedRun = {
  id: string;
  application_id: string;
  status: RunStatus;
  destination_url: string;
  destination_host: string;
  adapter: string | null;
  inputs: Record<string, string>;
  review: Review | null;
  review_hash: string | null;
  problems: Problem[];
  stop_reason: string | null;
  prepared_at: string | null;
  confirmed_at: string | null;
  submitted_at: string | null;
  confirmation_reference: string | null;
  nothing_submitted: boolean;
  created_at: string;
  updated_at: string;
  events: AuditEvent[];
};

export const RUN_LABELS: Record<RunStatus, string> = {
  preparing: "Filling the form…",
  needs_input: "Needs your input",
  awaiting_review: "Waiting for your review",
  submitting: "Submitting…",
  submitted: "Submitted",
  stopped: "Stopped",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const listRuns = (applicationId: string) =>
  apiFetch<AssistedRun[]>(`/api/v1/applications/${applicationId}/assisted-runs`);
export const startRun = (applicationId: string, inputs: Record<string, string> = {}) =>
  apiFetch<AssistedRun>(`/api/v1/applications/${applicationId}/assisted-runs`, {
    method: "POST",
    body: jsonBody({ inputs }),
  });
export const provideInputs = (runId: string, inputs: Record<string, string>) =>
  apiFetch<AssistedRun>(`/api/v1/assisted-runs/${runId}/inputs`, {
    method: "POST",
    body: jsonBody({ inputs }),
  });
/** Submit, only with the explicit confirmation of the exact review shown. */
export const submitRun = (runId: string, reviewHash: string) =>
  apiFetch<AssistedRun>(`/api/v1/assisted-runs/${runId}/submit`, {
    method: "POST",
    body: jsonBody({ review_hash: reviewHash, confirm: true }),
  });
export const cancelRun = (runId: string) =>
  apiFetch<AssistedRun>(`/api/v1/assisted-runs/${runId}/cancel`, { method: "POST" });
