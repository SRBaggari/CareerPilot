import { publicConfig } from "@/lib/config";

import { apiFetch, ApiError, jsonBody } from "./client";

/** A generated statement and the evidence it rests on. */
export type Claim = { claim_id: string | null; text: string; evidence_ids: string[] };

export type ExperienceEntry = {
  record_id: string;
  title: string;
  company_name: string;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  bullets: Claim[];
};

export type ProjectEntry = {
  record_id: string;
  title: string;
  role: string | null;
  url: string | null;
  start_date: string | null;
  end_date: string | null;
  bullets: Claim[];
};

export type ResumeContent = {
  header: {
    full_name: string;
    headline: string | null;
    contact_email: string | null;
    phone: string | null;
    location: string | null;
    website_url: string | null;
    linkedin_url: string | null;
    github_url: string | null;
  };
  summary: Claim[];
  skills: Claim[];
  experience: ExperienceEntry[];
  projects: ProjectEntry[];
  education: {
    record_id: string;
    institution: string;
    degree: string | null;
    field_of_study: string | null;
    start_date: string | null;
    end_date: string | null;
    gpa: string | null;
    gpa_scale: string | null;
  }[];
  certifications: {
    record_id: string;
    name: string;
    issuer: string | null;
    issue_date: string | null;
  }[];
  achievements: { record_id: string; title: string; achieved_on: string | null }[];
  coursework: { record_id: string; course_name: string }[];
};

export type AuditItem = {
  section: string;
  original_text: string;
  final_text: string | null;
  outcome: "rewritten" | "rejected";
  verdict: string;
  reason: string;
};

export type TailoredResume = {
  id: string;
  job_id: string;
  job_title: string;
  company_name: string;
  version: number;
  status: string;
  generator: string | null;
  created_at: string;
  updated_at: string;
  content: ResumeContent;
  verification: {
    verified_claims: number;
    rewritten: number;
    rejected: number;
    audit: AuditItem[];
  };
  notes: string[];
  evidence: Record<string, { content: string; record_label: string | null }>;
};

export type DownloadFormat = "pdf" | "docx";

const ROOT = "/api/v1/tailored-resumes";

export async function getLatestTailoredResume(jobId: string): Promise<TailoredResume | null> {
  try {
    return await apiFetch<TailoredResume>(`/api/v1/jobs/${jobId}/tailored-resumes/latest`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const generateTailoredResume = (jobId: string) =>
  apiFetch<TailoredResume>(`/api/v1/jobs/${jobId}/tailored-resumes`, { method: "POST" });

export const saveTailoredResume = (id: string, content: ResumeContent) =>
  apiFetch<TailoredResume>(`${ROOT}/${id}`, { method: "PUT", body: jsonBody({ content }) });

export const downloadUrl = (id: string, format: DownloadFormat) =>
  `${publicConfig.apiBaseUrl}${ROOT}/${id}/download?format=${format}`;

/** Error key the API uses for a claim, e.g. "summary[0]" or "experience:<id>[2]". */
export const claimKey = (section: string, position: number) => `${section}[${position}]`;
