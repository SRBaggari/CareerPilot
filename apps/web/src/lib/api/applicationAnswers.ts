import { apiFetch, jsonBody } from "./client";
import type { LetterAuditItem, LetterSentence } from "./coverLetters";
import type { VerificationReport } from "./verification";

export type QuestionType =
  "motivation" | "fit" | "project" | "skill" | "experience" | "behavioral" | "other";

export type ApplicationAnswer = {
  id: string;
  job_id: string;
  question: string;
  question_type: QuestionType;
  focus: string | null;
  understanding: string;
  position: number;
  max_words: number | null;
  status: "draft" | "verified" | "verification_failed" | "approved" | string;
  approved_at: string | null;
  generator: string | null;
  created_at: string;
  updated_at: string;
  sentences: LetterSentence[];
  text: string;
  word_count: number;
  evidence_used: { evidence_id: string; content: string; record_label: string | null }[];
  changes: { kept: number; regenerated: number; removed: number; audit: LetterAuditItem[] };
  notes: string[];
  report: VerificationReport | null;
};

export const QUESTION_TYPE_LABELS: Record<QuestionType, string> = {
  motivation: "Motivation",
  fit: "Fit",
  project: "Project",
  skill: "Skill",
  experience: "Experience",
  behavioral: "Situation",
  other: "General",
};

const jobPath = (jobId: string) => `/api/v1/jobs/${jobId}/application-answers`;
const ROOT = "/api/v1/application-answers";

export const listAnswers = (jobId: string) => apiFetch<ApplicationAnswer[]>(jobPath(jobId));

export const answerQuestions = (jobId: string, questions: string[], maxWords?: number) =>
  apiFetch<ApplicationAnswer[]>(jobPath(jobId), {
    method: "POST",
    body: jsonBody({ questions, max_words: maxWords ?? null }),
  });

export const saveAnswer = (id: string, answer: string) =>
  apiFetch<ApplicationAnswer>(`${ROOT}/${id}`, { method: "PUT", body: jsonBody({ answer }) });

export const regenerateAnswer = (id: string) =>
  apiFetch<ApplicationAnswer>(`${ROOT}/${id}/regenerate`, { method: "POST" });

export const approveAnswer = (id: string) =>
  apiFetch<ApplicationAnswer>(`${ROOT}/${id}/approve`, { method: "POST" });

export const deleteAnswer = (id: string) => apiFetch<void>(`${ROOT}/${id}`, { method: "DELETE" });
