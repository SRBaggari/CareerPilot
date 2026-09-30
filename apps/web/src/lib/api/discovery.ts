import { apiFetch } from "./client";

export type WorkMode = "onsite" | "hybrid" | "remote";
export type EmploymentType =
  "full_time" | "part_time" | "internship" | "contract" | "freelance" | "volunteer" | "other";
export type ExperienceLevel =
  "student" | "entry_level" | "junior" | "mid_level" | "senior" | "lead";

export type DiscoveredJob = {
  source: string;
  source_identifier: string;
  title: string;
  company: string;
  location: string | null;
  url: string | null;
  description: string;
  employment_type: EmploymentType | null;
  work_mode: WorkMode | null;
  posted_date: string | null;
  deadline: string | null;
  skills: string[];
  experience_level: ExperienceLevel | null;
  matched_skills: string[];
  imported_job_id: string | null;
};

export type JobSourceStatus = {
  name: string;
  display_name: string;
  access_kind: string | null;
  description: string;
  terms_url: string | null;
  enabled: boolean;
  reasons: string[];
};

export type DiscoveryResults = {
  jobs: DiscoveredJob[];
  total: number;
  page: number;
  page_size: number;
  sources: JobSourceStatus[];
  errors: string[];
};

export type DiscoveryFilters = {
  role: string;
  location: string;
  remote: "" | "true" | "false";
  employmentTypes: EmploymentType[];
  skills: string[];
  experienceLevels: ExperienceLevel[];
};

export const EMPTY_FILTERS: DiscoveryFilters = {
  role: "",
  location: "",
  remote: "",
  employmentTypes: [],
  skills: [],
  experienceLevels: [],
};

export const WORK_MODE_LABELS: Record<WorkMode, string> = {
  onsite: "On-site",
  hybrid: "Hybrid",
  remote: "Remote",
};

export const EMPLOYMENT_LABELS: Partial<Record<EmploymentType, string>> = {
  internship: "Internship",
  full_time: "Full-time",
  part_time: "Part-time",
  contract: "Contract",
};

export const LEVEL_LABELS: Record<ExperienceLevel, string> = {
  student: "Student / intern",
  entry_level: "Entry level",
  junior: "Junior",
  mid_level: "Mid level",
  senior: "Senior",
  lead: "Lead",
};

export function discoveryQuery(filters: DiscoveryFilters, page = 1, pageSize = 10): string {
  const params = new URLSearchParams();
  if (filters.role.trim()) params.set("role", filters.role.trim());
  if (filters.location.trim()) params.set("location", filters.location.trim());
  if (filters.remote) params.set("remote", filters.remote);
  filters.employmentTypes.forEach((t) => params.append("employment_type", t));
  filters.skills.forEach((s) => params.append("skills", s));
  filters.experienceLevels.forEach((l) => params.append("experience_level", l));
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  return params.toString();
}

export const searchJobs = (filters: DiscoveryFilters, page = 1, pageSize = 10) =>
  apiFetch<DiscoveryResults>(`/api/v1/discovery/jobs?${discoveryQuery(filters, page, pageSize)}`);

export const importPosting = (source: string, identifier: string) =>
  apiFetch<{ job_id: string; created: boolean }>(
    `/api/v1/discovery/jobs/${encodeURIComponent(source)}/${encodeURIComponent(identifier)}/import`,
    { method: "POST" },
  );
