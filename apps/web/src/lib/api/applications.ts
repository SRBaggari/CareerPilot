import { apiFetch, jsonBody } from "./client";
import type { ApprovalState } from "./review";

export type ApplicationStatus =
  | "discovered"
  | "saved"
  | "analyzed"
  | "application_prepared"
  | "awaiting_approval"
  | "submitted"
  | "assessment"
  | "interview"
  | "offer"
  | "rejected"
  | "withdrawn";

export const STATUSES: ApplicationStatus[] = [
  "discovered",
  "saved",
  "analyzed",
  "application_prepared",
  "awaiting_approval",
  "submitted",
  "assessment",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
];

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  discovered: "Discovered",
  saved: "Saved",
  analyzed: "Analyzed",
  application_prepared: "Application prepared",
  awaiting_approval: "Awaiting approval",
  submitted: "Submitted",
  assessment: "Assessment",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export type InterviewType =
  "phone_screen" | "technical" | "behavioral" | "take_home" | "onsite" | "panel" | "other";

export type Interview = {
  id: string;
  application_id: string;
  company: string;
  position: string;
  interview_type: InterviewType;
  status: "scheduled" | "completed" | "cancelled" | "no_show";
  scheduled_at: string | null;
  duration_minutes: number | null;
  location: string | null;
  meeting_url: string | null;
  notes: string | null;
};

export type FollowUp = {
  id: string;
  application_id: string;
  company: string;
  position: string;
  interview_id: string | null;
  channel: "email" | "phone" | "linkedin" | "other";
  status: "pending" | "done" | "skipped";
  due_at: string | null;
  completed_at: string | null;
  subject: string | null;
  notes: string | null;
  overdue: boolean;
};

export type ApplicationSummary = {
  id: string;
  job_id: string;
  company: string;
  position: string;
  location: string | null;
  job_url: string | null;
  status: ApplicationStatus;
  discovered_at: string | null;
  applied_at: string | null;
  approved_at: string | null;
  approval_state: ApprovalState;
  updated_at: string;
  next_interview_at: string | null;
  next_follow_up_at: string | null;
  overdue_follow_ups: number;
  has_resume: boolean;
  has_cover_letter: boolean;
  answers_approved: number;
  answers_total: number;
};

export type DocumentRef = {
  id: string;
  version: number;
  status: string;
  created_at: string;
  newer_version: number | null;
};

export type TimelineEvent = {
  at: string;
  kind: "created" | "status" | "approval" | "interview" | "follow_up" | "document" | "answer";
  title: string;
  detail: string | null;
  upcoming: boolean;
};

export type Application = ApplicationSummary & {
  notes: string | null;
  resume: DocumentRef | null;
  cover_letter: DocumentRef | null;
  answers: { id: string; question: string; status: string; approved: boolean }[];
  readiness: { label: string; ok: boolean; detail: string; required: boolean }[];
  approval_blockers: string[];
  allowed_statuses: ApplicationStatus[];
  interviews: Interview[];
  follow_ups: FollowUp[];
  timeline: TimelineEvent[];
};

export type Dashboard = {
  counts: Record<ApplicationStatus, number>;
  total: number;
  active: number;
  follow_ups_due: FollowUp[];
  upcoming_interviews: Interview[];
  recent: ApplicationSummary[];
};

export type ApplicationFilters = {
  statuses: ApplicationStatus[];
  q: string;
  followUpDue: boolean;
  sort: "updated" | "company" | "discovered" | "applied";
};

const ROOT = "/api/v1/applications";

export function filterQuery(f: ApplicationFilters): string {
  const params = new URLSearchParams();
  f.statuses.forEach((s) => params.append("status", s));
  if (f.q.trim()) params.set("q", f.q.trim());
  if (f.followUpDue) params.set("follow_up_due", "true");
  params.set("sort", f.sort);
  return params.toString();
}

export const listApplications = (f: ApplicationFilters) =>
  apiFetch<ApplicationSummary[]>(`${ROOT}?${filterQuery(f)}`);
export const getDashboard = () => apiFetch<Dashboard>(`${ROOT}/dashboard`);
export const getApplication = (id: string) => apiFetch<Application>(`${ROOT}/${id}`);
export const trackJob = (jobId: string) =>
  apiFetch<Application>(ROOT, { method: "POST", body: jsonBody({ job_id: jobId }) });
export const updateApplication = (id: string, body: Record<string, unknown>) =>
  apiFetch<Application>(`${ROOT}/${id}`, { method: "PATCH", body: jsonBody(body) });
export const changeStatus = (
  id: string,
  status: ApplicationStatus,
  extra: { note?: string; submitted_on?: string } = {},
) =>
  apiFetch<Application>(`${ROOT}/${id}/status`, {
    method: "POST",
    body: jsonBody({ status, ...extra }),
  });
export const addInterview = (id: string, body: Record<string, unknown>) =>
  apiFetch<Application>(`${ROOT}/${id}/interviews`, { method: "POST", body: jsonBody(body) });
export const updateInterview = (id: string, interviewId: string, body: Record<string, unknown>) =>
  apiFetch<Application>(`${ROOT}/${id}/interviews/${interviewId}`, {
    method: "PATCH",
    body: jsonBody(body),
  });
export const addFollowUp = (id: string, body: Record<string, unknown>) =>
  apiFetch<Application>(`${ROOT}/${id}/follow-ups`, { method: "POST", body: jsonBody(body) });
export const updateFollowUp = (id: string, followUpId: string, body: Record<string, unknown>) =>
  apiFetch<Application>(`${ROOT}/${id}/follow-ups/${followUpId}`, {
    method: "PATCH",
    body: jsonBody(body),
  });

export const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("en-GB", { dateStyle: "medium" }) : "—";
export const formatDateTime = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })
    : "—";
