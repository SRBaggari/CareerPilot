import { publicConfig } from "@/lib/config";

import { apiFetch, ApiError, jsonBody } from "./client";
import type { VerificationReport, VerificationStatus } from "./verification";

export type LetterSentence = { claim_id: string | null; text: string; evidence_ids: string[] };

export type CoverLetterContent = {
  job_title: string;
  company_name: string;
  signature: {
    full_name: string;
    contact_email: string | null;
    phone: string | null;
    location: string | null;
  };
  greeting: string;
  paragraphs: { sentences: LetterSentence[] }[];
  closing: string;
};

export type LetterAuditItem = {
  section: string;
  original_text: string;
  final_text: string | null;
  outcome: "regenerated" | "removed";
  verdict: VerificationStatus;
  reason: string;
};

export type CoverLetter = {
  id: string;
  job_id: string;
  job_title: string;
  company_name: string;
  version: number;
  status: string;
  generator: string | null;
  created_at: string;
  updated_at: string;
  content: CoverLetterContent;
  word_count: number;
  changes: { kept: number; regenerated: number; removed: number; audit: LetterAuditItem[] };
  notes: string[];
  evidence: Record<string, { content: string; record_label: string | null }>;
  report: VerificationReport | null;
};

export type CoverLetterEdit = { greeting: string; paragraphs: string[]; closing: string };

const ROOT = "/api/v1/cover-letters";

export const paragraphText = (p: { sentences: LetterSentence[] }) =>
  p.sentences.map((s) => s.text).join(" ");

export async function getLatestCoverLetter(jobId: string): Promise<CoverLetter | null> {
  try {
    return await apiFetch<CoverLetter>(`/api/v1/jobs/${jobId}/cover-letters/latest`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const generateCoverLetter = (jobId: string) =>
  apiFetch<CoverLetter>(`/api/v1/jobs/${jobId}/cover-letters`, { method: "POST" });

export const saveCoverLetter = (id: string, edit: CoverLetterEdit) =>
  apiFetch<CoverLetter>(`${ROOT}/${id}`, { method: "PUT", body: jsonBody(edit) });

export const reverifyCoverLetter = (id: string) =>
  apiFetch<CoverLetter>(`${ROOT}/${id}/verify`, { method: "POST" });

export const coverLetterDownloadUrl = (id: string, format: "pdf" | "docx") =>
  `${publicConfig.apiBaseUrl}${ROOT}/${id}/download?format=${format}`;
