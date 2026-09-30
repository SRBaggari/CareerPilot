import { apiFetch, jsonBody } from "./client";

export type VerificationStatus =
  "supported" | "partially_supported" | "unsupported" | "contradicted";

export type ClaimResult = {
  claim_text: string;
  claim_type: string;
  section: string;
  position: number;
  record_id: string | null;
  cited_evidence_ids: string[];
  evidence_ids: string[];
  evidence_source: "cited" | "retrieved" | "profile" | "none";
  verification_status: VerificationStatus;
  confidence: number;
  reason: string;
  method: "rule_based" | "llm";
};

export type VerificationReport = {
  id: string | null;
  document_type: "tailored_resume" | "cover_letter" | "text";
  document_id: string | null;
  trigger: "generation" | "edit" | "manual" | null;
  created_at: string | null;
  verifier: string;
  outcome: "approved" | "rejected";
  counts: Record<VerificationStatus, number>;
  claims: ClaimResult[];
  warnings: string[];
};

export const STATUS_ORDER: VerificationStatus[] = [
  "contradicted",
  "unsupported",
  "partially_supported",
  "supported",
];

export const STATUS_LABELS: Record<VerificationStatus, string> = {
  supported: "Supported",
  partially_supported: "Partially supported",
  unsupported: "Unsupported",
  contradicted: "Contradicted",
};

export const STATUS_HELP: Record<VerificationStatus, string> = {
  supported: "Your evidence directly supports it.",
  partially_supported: "Your evidence supports part of it, not all.",
  unsupported: "No verified evidence supports it.",
  contradicted: "Your stored information conflicts with it.",
};

/** Verify claims (or free text, split into sentences) against your evidence. */
export const checkClaims = (text: string) =>
  apiFetch<VerificationReport>("/api/v1/verification/check", {
    method: "POST",
    body: jsonBody({ text }),
  });
