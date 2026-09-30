import type { EvidenceSourceType, SectionItem, SectionKey } from "@/lib/api/profile";

import type { FieldDef } from "./form";
import {
  DEGREE_LEVEL_LABELS,
  DEGREE_LEVEL_OPTIONS,
  JOB_TYPE_LABELS,
  JOB_TYPE_OPTIONS,
  label,
} from "./options";

export type SectionDef = {
  key: SectionKey;
  path: string; // API path segment
  title: string;
  singular: string;
  evidenceType: EvidenceSourceType;
  evidenceHint: string;
  fields: FieldDef[];
  summarize: (item: SectionItem) => { title: string; subtitle?: string; meta?: string };
};

const MONTH = new Intl.DateTimeFormat("en", { month: "short", year: "numeric", timeZone: "UTC" });

export function formatDate(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? value : MONTH.format(date);
}

export function formatRange(start: unknown, end: unknown, current = false): string {
  const from = formatDate(start);
  const to = current ? "Present" : formatDate(end);
  if (from && to) return `${from} – ${to}`;
  return from || to;
}

const join = (...parts: unknown[]) =>
  parts.filter((p) => typeof p === "string" && p.trim()).join(" · ");

const dates = (start: string, end: string): FieldDef[] => [
  { name: start, label: "Start date", type: "date" },
  { name: end, label: "End date", type: "date" },
];

export const SECTIONS: SectionDef[] = [
  {
    key: "work_experiences",
    path: "work-experiences",
    title: "Work experience",
    singular: "work experience",
    evidenceType: "work_experience",
    evidenceHint: "e.g. Reduced report generation time by 40% by caching SQL queries.",
    fields: [
      { name: "title", label: "Job title", type: "text", required: true, maxLength: 200 },
      { name: "company_name", label: "Company", type: "text", required: true, maxLength: 300 },
      { name: "employment_type", label: "Type", type: "select", options: JOB_TYPE_OPTIONS },
      { name: "location", label: "Location", type: "text", maxLength: 200 },
      ...dates("start_date", "end_date"),
      { name: "is_current", label: "I currently work here", type: "checkbox" },
      { name: "description", label: "Description", type: "textarea", maxLength: 5000, wide: true },
    ],
    summarize: (i) => ({
      title: String(i.title),
      subtitle: join(i.company_name, label(JOB_TYPE_LABELS, i.employment_type), i.location),
      meta: formatRange(i.start_date, i.end_date, Boolean(i.is_current)),
    }),
  },
  {
    key: "projects",
    path: "projects",
    title: "Projects",
    singular: "project",
    evidenceType: "project",
    evidenceHint: "e.g. Implemented a RAG pipeline using document retrieval and QA.",
    fields: [
      { name: "title", label: "Title", type: "text", required: true, maxLength: 300 },
      { name: "role", label: "Your role", type: "text", maxLength: 200 },
      { name: "project_url", label: "Project URL", type: "url" },
      { name: "repository_url", label: "Repository URL", type: "url" },
      ...dates("start_date", "end_date"),
      { name: "description", label: "Description", type: "textarea", maxLength: 5000, wide: true },
    ],
    summarize: (i) => ({
      title: String(i.title),
      subtitle: join(i.role),
      meta: formatRange(i.start_date, i.end_date),
    }),
  },
  {
    key: "educations",
    path: "educations",
    title: "Education",
    singular: "education",
    evidenceType: "education",
    evidenceHint: "e.g. Thesis on graph neural networks for molecule property prediction.",
    fields: [
      { name: "institution", label: "Institution", type: "text", required: true, maxLength: 300 },
      { name: "degree", label: "Degree", type: "text", maxLength: 200, placeholder: "B.Tech" },
      { name: "degree_level", label: "Level", type: "select", options: DEGREE_LEVEL_OPTIONS },
      { name: "field_of_study", label: "Field of study", type: "text", maxLength: 200 },
      { name: "location", label: "Location", type: "text", maxLength: 200 },
      ...dates("start_date", "end_date"),
      { name: "gpa", label: "GPA", type: "number", step: "0.01" },
      { name: "gpa_scale", label: "GPA scale", type: "number", step: "0.01", placeholder: "4.0" },
      { name: "description", label: "Description", type: "textarea", maxLength: 5000, wide: true },
    ],
    summarize: (i) => ({
      title: String(i.institution),
      subtitle: join(
        i.degree,
        i.field_of_study,
        i.degree ? "" : label(DEGREE_LEVEL_LABELS, i.degree_level),
        i.gpa && i.gpa_scale ? `GPA ${String(i.gpa)}/${String(i.gpa_scale)}` : "",
      ),
      meta: formatRange(i.start_date, i.end_date),
    }),
  },
  {
    key: "certifications",
    path: "certifications",
    title: "Certifications",
    singular: "certification",
    evidenceType: "certification",
    evidenceHint: "e.g. Covered IAM, VPC design, and cost optimization.",
    fields: [
      { name: "name", label: "Name", type: "text", required: true, maxLength: 300 },
      { name: "issuer", label: "Issuer", type: "text", maxLength: 200 },
      { name: "issue_date", label: "Issued", type: "date" },
      { name: "expiration_date", label: "Expires", type: "date" },
      { name: "credential_id", label: "Credential ID", type: "text", maxLength: 200 },
      { name: "credential_url", label: "Credential URL", type: "url" },
    ],
    summarize: (i) => ({
      title: String(i.name),
      subtitle: join(i.issuer),
      meta: formatDate(i.issue_date),
    }),
  },
  {
    key: "achievements",
    path: "achievements",
    title: "Achievements",
    singular: "achievement",
    evidenceType: "achievement",
    evidenceHint: "e.g. Placed 1st of 120 teams at the university hackathon.",
    fields: [
      { name: "title", label: "Title", type: "text", required: true, maxLength: 300 },
      { name: "issuer", label: "Awarded by", type: "text", maxLength: 200 },
      { name: "achieved_on", label: "Date", type: "date" },
      { name: "url", label: "URL", type: "url" },
      { name: "description", label: "Description", type: "textarea", maxLength: 5000, wide: true },
    ],
    summarize: (i) => ({
      title: String(i.title),
      subtitle: join(i.issuer),
      meta: formatDate(i.achieved_on),
    }),
  },
  {
    key: "coursework",
    path: "coursework",
    title: "Coursework",
    singular: "course",
    evidenceType: "coursework",
    evidenceHint: "e.g. Final project: sentiment classifier with 91% accuracy.",
    fields: [
      { name: "course_name", label: "Course", type: "text", required: true, maxLength: 300 },
      { name: "course_code", label: "Code", type: "text", maxLength: 50 },
      { name: "education_id", label: "Part of", type: "education" },
      { name: "term", label: "Term", type: "text", maxLength: 100, placeholder: "Fall 2023" },
      { name: "grade", label: "Grade", type: "text", maxLength: 20 },
      { name: "description", label: "Description", type: "textarea", maxLength: 5000, wide: true },
    ],
    summarize: (i) => ({
      title: String(i.course_name),
      subtitle: join(i.course_code, i.term),
      meta: typeof i.grade === "string" ? `Grade ${i.grade}` : "",
    }),
  },
];
