import { apiFetch, ApiError, jsonBody } from "./client";

export type EvidenceSourceType =
  | "project"
  | "work_experience"
  | "education"
  | "certification"
  | "achievement"
  | "coursework"
  | "profile";

export type EvidenceOrigin = "user_entered" | "resume_extracted" | "ai_suggested";

export type Evidence = {
  id: string;
  source_type: EvidenceSourceType;
  subject_id: string | null;
  content: string;
  origin: EvidenceOrigin;
  confirmed_at: string | null;
  is_cited: boolean;
  created_at: string;
  updated_at: string;
};

/** A row in one of the list sections. Field names match the section's form definition. */
export type SectionItem = {
  id: string;
  sort_order: number;
  evidence: Evidence[];
  [field: string]: unknown;
};

export type Skill = {
  id: string;
  skill_id: string;
  name: string;
  category: string | null;
  proficiency: string | null;
  years_experience: string | null;
};

export type PersonalInfo = {
  full_name: string;
  headline: string | null;
  summary: string | null;
  contact_email: string | null;
  phone: string | null;
  location: string | null;
  website_url: string | null;
  linkedin_url: string | null;
  github_url: string | null;
};

export type Preferences = {
  preferred_roles: string[];
  preferred_locations: string[];
  work_modes: string[];
  job_types: string[];
  experience_level: string | null;
};

export type SectionKey =
  "educations" | "work_experiences" | "projects" | "certifications" | "achievements" | "coursework";

export type Profile = PersonalInfo &
  Preferences &
  Record<SectionKey, SectionItem[]> & {
    id: string;
    skills: Skill[];
    evidence: Evidence[];
    pending_suggestions: number;
    created_at: string;
    updated_at: string;
  };

export type Suggestion = {
  id: string;
  section: string;
  action: "create" | "update";
  target_id: string | null;
  proposed_data: Record<string, unknown>;
  source: "resume_extraction" | "ai_generation";
  rationale: string | null;
  status: "pending" | "accepted" | "rejected";
  resume_id: string | null;
  source_excerpt: string | null;
  accepted_data: Record<string, unknown> | null;
  created_at: string;
};

const BASE = "/api/v1/profile";

export async function getProfile(): Promise<Profile | null> {
  try {
    return await apiFetch<Profile>(BASE);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const createProfile = (data: Partial<PersonalInfo & Preferences>) =>
  apiFetch<Profile>(BASE, { method: "POST", body: jsonBody(data) });

export const updateProfile = (patch: Partial<PersonalInfo & Preferences>) =>
  apiFetch<Profile>(BASE, { method: "PATCH", body: jsonBody(patch) });

export const deleteProfile = () => apiFetch<void>(BASE, { method: "DELETE" });

export const createItem = (path: string, data: Record<string, unknown>) =>
  apiFetch<SectionItem>(`${BASE}/${path}`, { method: "POST", body: jsonBody(data) });

export const replaceItem = (path: string, id: string, data: Record<string, unknown>) =>
  apiFetch<SectionItem>(`${BASE}/${path}/${id}`, { method: "PUT", body: jsonBody(data) });

export const deleteItem = (path: string, id: string) =>
  apiFetch<void>(`${BASE}/${path}/${id}`, { method: "DELETE" });

export const addEvidence = (data: {
  source_type: EvidenceSourceType;
  subject_id: string | null;
  content: string;
}) => apiFetch<Evidence>(`${BASE}/evidence`, { method: "POST", body: jsonBody(data) });

export const updateEvidence = (id: string, content: string) =>
  apiFetch<Evidence>(`${BASE}/evidence/${id}`, { method: "PATCH", body: jsonBody({ content }) });

export const deleteEvidence = (id: string) =>
  apiFetch<void>(`${BASE}/evidence/${id}`, { method: "DELETE" });

export const addSkill = (data: {
  name: string;
  category: string | null;
  proficiency: string | null;
  years_experience: string | null;
}) => apiFetch<Skill>(`${BASE}/skills`, { method: "POST", body: jsonBody(data) });

export const updateSkill = (
  id: string,
  data: { proficiency: string | null; years_experience: string | null },
) => apiFetch<Skill>(`${BASE}/skills/${id}`, { method: "PUT", body: jsonBody(data) });

export const deleteSkill = (id: string) =>
  apiFetch<void>(`${BASE}/skills/${id}`, { method: "DELETE" });

export const listSuggestions = () => apiFetch<Suggestion[]>(`${BASE}/suggestions`);

/** Accept as proposed, or pass the user's edited version of the proposed data. */
export const acceptSuggestion = (id: string, editedData?: Record<string, unknown>) =>
  apiFetch<Suggestion>(`${BASE}/suggestions/${id}/accept`, {
    method: "POST",
    body: editedData ? jsonBody({ proposed_data: editedData }) : undefined,
  });

export const rejectSuggestion = (id: string) =>
  apiFetch<Suggestion>(`${BASE}/suggestions/${id}/reject`, { method: "POST" });
