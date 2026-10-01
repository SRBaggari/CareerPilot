import { apiFetch, jsonBody } from "./client";
import type { ApplicationStatus } from "./applications";
import type { CoverLetterContent } from "./coverLetters";
import type { ResumeContent } from "./tailoredResumes";

export type ApprovalState = "draft" | "ready_for_review" | "approved" | "rejected" | "submitted";

export const APPROVAL_LABELS: Record<ApprovalState, string> = {
  draft: "Draft",
  ready_for_review: "Ready for review",
  approved: "Approved",
  rejected: "Rejected",
  submitted: "Submitted",
};

export type Verification = {
  document_status: string;
  verified: boolean;
  outcome: "approved" | "rejected" | null;
  verifier: string | null;
  checked_at: string | null;
  counts: Record<string, number>;
  claims_verified: number;
  unverified: { text: string; status: string; reason: string | null }[];
};

export type Issue = {
  section: string;
  severity: "blocker" | "warning";
  message: string;
};

export type ReviewPackage = {
  application_id: string;
  status: ApplicationStatus;
  approval_state: ApprovalState;
  job: { id: string; title: string; company: string; location: string | null; url: string | null };
  personal: {
    full_name: string;
    email: string;
    email_source: "profile" | "account";
    phone: string | null;
    location: string | null;
    linkedin: string | null;
  };
  resume: {
    id: string;
    version: number;
    status: string;
    content: ResumeContent;
    verification: Verification;
  } | null;
  cover_letter: {
    id: string;
    version: number;
    status: string;
    content: CoverLetterContent;
    verification: Verification;
  } | null;
  answers: {
    id: string;
    question: string;
    answer: string;
    status: string;
    approved: boolean;
    verification: Verification;
  }[];
  issues: Issue[];
  content_hash: string;
  approval: {
    reviewer: string | null;
    approved_at: string;
    content_hash: string;
    version: number | null;
  } | null;
  submitted_at: string | null;
  can_request_review: boolean;
  can_approve: boolean;
  can_submit: boolean;
  submit_blockers: string[];
  history: {
    version: number;
    decision: "approved" | "rejected";
    reviewer: string;
    at: string;
    content_hash: string;
    note: string | null;
  }[];
  events: {
    id: string;
    at: string;
    actor: "user" | "system" | "automation";
    user: string | null;
    action: string;
    message: string;
    detail: Record<string, unknown>;
  }[];
};

const ROOT = "/api/v1/applications";

/** Opening the review is recorded; it never approves anything. */
export const getReview = (id: string) => apiFetch<ReviewPackage>(`${ROOT}/${id}/review`);
export const requestReview = (id: string) =>
  apiFetch<ReviewPackage>(`${ROOT}/${id}/review/request`, { method: "POST" });
/** The explicit approval of exactly the content version shown (its hash). */
export const approveApplication = (id: string, contentHash: string, note?: string) =>
  apiFetch<ReviewPackage>(`${ROOT}/${id}/approve`, {
    method: "POST",
    body: jsonBody({ content_hash: contentHash, confirm: true, ...(note ? { note } : {}) }),
  });
export const rejectApplication = (id: string, note?: string) =>
  apiFetch<ReviewPackage>(`${ROOT}/${id}/reject`, {
    method: "POST",
    body: jsonBody(note ? { note } : {}),
  });
