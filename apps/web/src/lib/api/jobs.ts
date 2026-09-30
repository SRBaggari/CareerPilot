import { apiFetch, jsonBody } from "./client";

export type Importance = "required" | "preferred" | "informational";
export type RequirementType =
  | "skill"
  | "technology"
  | "experience"
  | "education"
  | "certification"
  | "language"
  | "eligibility"
  | "responsibility"
  | "other";

export type Requirement = {
  id: string;
  requirement_type: RequirementType;
  importance: Importance;
  description: string;
  source_excerpt: string | null;
  min_years: string | null;
  skill_id: string | null;
};

export type Salary = {
  text: string | null;
  minimum: string | null;
  maximum: string | null;
  currency: string | null;
  period: "hour" | "day" | "week" | "month" | "year" | null;
};

export type JobSummary = {
  id: string;
  title: string;
  company_name: string;
  location: string | null;
  workplace_type: "onsite" | "hybrid" | "remote" | null;
  employment_type: string | null;
  application_deadline: string | null;
  input_method: "pasted_text" | "manual_entry" | null;
  requirement_counts: Record<Importance, number>;
  created_at: string;
};

export type Job = JobSummary & {
  source_url: string | null;
  description: string | null;
  salary: Salary | null;
  analyzer_name: string | null;
  analysis_warnings: string[];
  analyzed_at: string | null;
  requirements: Requirement[];
};

export type AnalyzeInput = {
  description: string;
  source_url?: string | null;
  title?: string | null;
  company_name?: string | null;
  location?: string | null;
};

export type ManualRequirement = {
  requirement_type: RequirementType;
  importance: Importance;
  description: string;
  min_years?: string | null;
};

export type ManualJobInput = {
  title: string;
  company_name: string;
  location?: string | null;
  workplace_type?: string | null;
  employment_type?: string | null;
  description?: string | null;
  source_url?: string | null;
  application_deadline?: string | null;
  requirements: ManualRequirement[];
};

const BASE = "/api/v1/jobs";

export const analyzeJob = (input: AnalyzeInput) =>
  apiFetch<Job>(`${BASE}/analyze`, { method: "POST", body: jsonBody(input) });
export const createJob = (input: ManualJobInput) =>
  apiFetch<Job>(BASE, { method: "POST", body: jsonBody(input) });
export const listJobs = () => apiFetch<JobSummary[]>(BASE);
export const getJob = (id: string) => apiFetch<Job>(`${BASE}/${id}`);
export const deleteJob = (id: string) => apiFetch<void>(`${BASE}/${id}`, { method: "DELETE" });
