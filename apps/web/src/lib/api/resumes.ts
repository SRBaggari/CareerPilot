import { apiFetch } from "./client";

export type Resume = {
  id: string;
  file_name: string;
  file_format: "pdf" | "docx";
  file_size_bytes: number;
  parse_status: "pending" | "parsed" | "failed";
  parse_error: string | null;
  parse_warnings: string[];
  parser_name: string | null;
  is_primary: boolean;
  pending_suggestions: number;
  total_suggestions: number;
  created_at: string;
};

export type ResumeDetail = Resume & { parsed_text: string | null };

const BASE = "/api/v1/resumes";
export const ACCEPTED_TYPES = ".pdf,.docx";
export const MAX_RESUME_MB = 5;

export function uploadResume(file: File): Promise<ResumeDetail> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<ResumeDetail>(BASE, { method: "POST", body: form });
}

export const listResumes = () => apiFetch<Resume[]>(BASE);
export const getResume = (id: string) => apiFetch<ResumeDetail>(`${BASE}/${id}`);
export const deleteResume = (id: string) => apiFetch<void>(`${BASE}/${id}`, { method: "DELETE" });
