/** Test helper: a fake CareerPilot API behind a stubbed global fetch. */
import { vi } from "vitest";

import type { Profile } from "@/lib/api/profile";

export const API_ROOT = "http://localhost:8000/api/v1";

export type Call = { key: string; body: unknown };
export type Handler = (body: unknown) => { status: number; body?: unknown };

export function makeProfile(overrides: Partial<Profile> = {}): Profile {
  return {
    id: "p1",
    full_name: "Test Candidate",
    headline: null,
    summary: null,
    contact_email: null,
    phone: null,
    location: null,
    website_url: null,
    linkedin_url: null,
    github_url: null,
    preferred_roles: [],
    preferred_locations: [],
    work_modes: [],
    job_types: [],
    experience_level: null,
    educations: [],
    work_experiences: [],
    projects: [],
    certifications: [],
    achievements: [],
    coursework: [],
    skills: [],
    evidence: [],
    pending_suggestions: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

/**
 * Stub fetch. Routes are keyed like "GET /profile" or "POST /resumes"; unmatched GET
 * /resumes returns an empty list so components that list resumes don't need setup.
 */
export function stubApi(routes: Record<string, Handler>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url.replace(API_ROOT, "")}`;
      const raw = init?.body;
      const body =
        raw instanceof FormData ? raw : typeof raw === "string" ? JSON.parse(raw) : undefined;
      calls.push({ key, body });
      const handler =
        routes[key] ?? (key === "GET /resumes" ? () => ({ status: 200, body: [] }) : undefined);
      if (!handler)
        return new Response(JSON.stringify({ detail: `unmocked ${key}` }), { status: 500 });
      const result = handler(body);
      return new Response(result.body === undefined ? null : JSON.stringify(result.body), {
        status: result.status,
      });
    }),
  );
  return calls;
}
