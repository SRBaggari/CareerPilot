import { apiFetch } from "./client";
import type { EmploymentType, ExperienceLevel, JobSourceStatus, WorkMode } from "./discovery";
import type { MatchStatus } from "./matching";

export type RecommendationView = "recommended" | "saved" | "ignored" | "filtered_out";

export type SkillResult = { skill: string; importance: string; explanation: string };

export type Explanation = {
  summary: string;
  reasons: string[];
  required_met: number;
  required_partial: number;
  required_total: number;
  matched_skills: SkillResult[];
  partial_skills: SkillResult[];
  missing_skills: SkillResult[];
  requirements: {
    requirement: string;
    requirement_type: string;
    importance: string;
    status: MatchStatus;
    explanation: string;
  }[];
  relevant_projects: {
    project_id: string | null;
    title: string;
    evidence: string[];
    supports: string[];
  }[];
  preference_fit: string[];
  disclaimer: string;
};

export type Recommendation = {
  id: string;
  source: string;
  source_identifier: string;
  title: string;
  company: string;
  location: string | null;
  url: string | null;
  work_mode: WorkMode | null;
  employment_type: EmploymentType | null;
  experience_level: ExperienceLevel | null;
  posted_date: string | null;
  deadline: string | null;
  status: "new" | "saved" | "ignored";
  eligible: boolean;
  exclusions: string[];
  concerns: string[];
  explanation: Explanation;
  required_coverage: number | null;
  overall_coverage: number | null;
  rank: number | null;
  computed_at: string;
  is_stale: boolean;
  job_id: string | null;
  application: { id: string; status: string } | null;
};

export type RecommendationList = {
  recommendations: Recommendation[];
  counts: Record<RecommendationView, number>;
  refreshed_at: string | null;
  errors: string[];
  sources: JobSourceStatus[];
};

export type ActionResult = {
  recommendation: Recommendation;
  job_id: string | null;
  application_id: string | null;
};

const ROOT = "/api/v1/recommendations";

export const listRecommendations = (view: RecommendationView) =>
  apiFetch<RecommendationList>(`${ROOT}?view=${view}`);

export const refreshRecommendations = () =>
  apiFetch<RecommendationList>(`${ROOT}/refresh`, { method: "POST" });

export type RecommendationAction = "save" | "ignore" | "restore" | "analyze" | "start-application";

export const actOn = (id: string, action: RecommendationAction) =>
  apiFetch<ActionResult>(`${ROOT}/${id}/${action}`, { method: "POST" });
