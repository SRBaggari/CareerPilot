import { apiFetch } from "./client";

export type Health = { status: "ok"; service: string; environment: string };
export type Readiness = {
  status: "ready" | "not_ready";
  database: { connected: boolean; pgvector: boolean; detail: string | null };
};

export const getHealth = () => apiFetch<Health>("/health");
export const getReadiness = () => apiFetch<Readiness>("/health/ready");
