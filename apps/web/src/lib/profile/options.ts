/** Display labels for API enum values. Keys must match the backend enums. */

export type Option = { value: string; label: string };

const toOptions = (labels: Record<string, string>): Option[] =>
  Object.entries(labels).map(([value, label]) => ({ value, label }));

export const WORK_MODE_LABELS: Record<string, string> = {
  onsite: "On-site",
  hybrid: "Hybrid",
  remote: "Remote",
};

export const JOB_TYPE_LABELS: Record<string, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  internship: "Internship",
  contract: "Contract",
  freelance: "Freelance",
  volunteer: "Volunteer",
  other: "Other",
};

export const EXPERIENCE_LEVEL_LABELS: Record<string, string> = {
  student: "Student",
  entry_level: "Entry level",
  junior: "Junior",
  mid_level: "Mid level",
  senior: "Senior",
  lead: "Lead",
};

export const DEGREE_LEVEL_LABELS: Record<string, string> = {
  high_school: "High school",
  certificate: "Certificate",
  diploma: "Diploma",
  associate: "Associate",
  bachelor: "Bachelor's",
  master: "Master's",
  doctorate: "Doctorate",
  other: "Other",
};

export const SKILL_CATEGORY_LABELS: Record<string, string> = {
  programming_language: "Programming language",
  framework: "Framework",
  library: "Library",
  tool: "Tool",
  platform: "Platform",
  database: "Database",
  cloud: "Cloud",
  methodology: "Methodology",
  domain: "Domain knowledge",
  soft_skill: "Soft skill",
  language: "Spoken language",
  other: "Other",
};

export const PROFICIENCY_LABELS: Record<string, string> = {
  beginner: "Beginner",
  intermediate: "Intermediate",
  advanced: "Advanced",
  expert: "Expert",
};

export const WORK_MODE_OPTIONS = toOptions(WORK_MODE_LABELS);
export const JOB_TYPE_OPTIONS = toOptions(JOB_TYPE_LABELS);
export const EXPERIENCE_LEVEL_OPTIONS = toOptions(EXPERIENCE_LEVEL_LABELS);
export const DEGREE_LEVEL_OPTIONS = toOptions(DEGREE_LEVEL_LABELS);
export const SKILL_CATEGORY_OPTIONS = toOptions(SKILL_CATEGORY_LABELS);
export const PROFICIENCY_OPTIONS = toOptions(PROFICIENCY_LABELS);

export const label = (labels: Record<string, string>, value: unknown): string =>
  typeof value === "string" ? (labels[value] ?? value) : "";
