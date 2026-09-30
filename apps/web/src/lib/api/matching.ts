import { apiFetch, ApiError } from "./client";
import type { Importance, RequirementType } from "./jobs";

export type MatchStatus = "matched" | "partial" | "missing" | "unknown";

export type MatchingEvidence = {
  evidence_id: string;
  factual_content: string;
  similarity: number | null;
  source: {
    record_type: string;
    record_id: string | null;
    record_label: string | null;
    origin: string;
    resume_id: string | null;
    resume_file_name: string | null;
  };
};

export type RequirementMatch = {
  requirement_id: string;
  requirement: string;
  requirement_type: RequirementType;
  importance: Importance;
  matching_candidate_evidence: MatchingEvidence[];
  semantic_similarity: number | null;
  match_status: MatchStatus;
  explanation: string;
  evidence_ids: string[];
  judge: string;
  details: Record<string, unknown>;
};

export type MatchReport = {
  job_id: string;
  job_title: string;
  company_name: string;
  candidate_id: string;
  computed_at: string;
  matcher: string | null;
  embedding_model: string | null;
  scoring_version: string;
  disclaimer: string;
  summary: string | null;
  scores: {
    evidence_coverage: number;
    required_coverage: number | null;
    preferred_coverage: number | null;
    semantic_similarity: number | null;
    skill_coverage: number | null;
  };
  status_counts: Record<MatchStatus, number>;
  requirements: RequirementMatch[];
  strongest_matches: RequirementMatch[];
  missing_skills: RequirementMatch[];
  partial_matches: RequirementMatch[];
  potentially_disqualifying: RequirementMatch[];
  informational_not_scored: number;
  warnings: string[];
  is_stale: boolean;
};

const path = (jobId: string) => `/api/v1/jobs/${jobId}/match`;

export async function getMatch(jobId: string): Promise<MatchReport | null> {
  try {
    return await apiFetch<MatchReport>(path(jobId));
  } catch (error) {
    // 404 means "not computed yet" (or no profile/job, which computeMatch explains).
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const computeMatch = (jobId: string) =>
  apiFetch<MatchReport>(path(jobId), { method: "POST" });
