import type { Importance, Requirement, RequirementType, Salary } from "@/lib/api/jobs";
import type { Option } from "@/lib/profile/options";

export const IMPORTANCE_LABELS: Record<Importance, string> = {
  required: "Required",
  preferred: "Preferred",
  informational: "Informational",
};

export const REQUIREMENT_TYPE_LABELS: Record<RequirementType, string> = {
  skill: "Skill",
  technology: "Technology",
  experience: "Experience",
  education: "Education",
  certification: "Certification",
  language: "Language",
  eligibility: "Eligibility",
  responsibility: "Responsibility",
  other: "Other",
};

export const IMPORTANCE_OPTIONS: Option[] = Object.entries(IMPORTANCE_LABELS).map(
  ([value, label]) => ({
    value,
    label,
  }),
);
export const REQUIREMENT_TYPE_OPTIONS: Option[] = Object.entries(REQUIREMENT_TYPE_LABELS).map(
  ([value, label]) => ({ value, label }),
);

const SKILL_TYPES: RequirementType[] = ["technology", "skill", "language"];

/** Requirements grouped the way the Job Analysis page presents them. */
export function groupRequirements(requirements: Requirement[]) {
  const where = (types: RequirementType[], importance?: Importance) =>
    requirements.filter(
      (r) =>
        types.includes(r.requirement_type) &&
        (importance === undefined || r.importance === importance),
    );
  return {
    requiredSkills: where(SKILL_TYPES, "required"),
    preferredSkills: where(SKILL_TYPES, "preferred"),
    mentionedTechnologies: where(["technology"], "informational"),
    education: where(["education"]),
    experience: where(["experience"]),
    certifications: where(["certification"]),
    responsibilities: where(["responsibility"]),
    eligibility: where(["eligibility"]),
    other: where(["other"]),
  };
}

export function formatSalary(salary: Salary): string | null {
  const amount = (value: string | null) =>
    value === null ? null : Number(value).toLocaleString("en-US");
  const [min, max] = [amount(salary.minimum), amount(salary.maximum)];
  if (!min && !max) return null;
  const range = min && max ? `${min} – ${max}` : (min ?? max);
  const currency = salary.currency ? `${salary.currency} ` : "";
  const period = salary.period ? ` per ${salary.period}` : "";
  return `${currency}${range}${period}`;
}

/** "2026-07-31" -> "31 July 2026" (dates are calendar dates, so no timezone shift). */
export function formatLongDate(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}
