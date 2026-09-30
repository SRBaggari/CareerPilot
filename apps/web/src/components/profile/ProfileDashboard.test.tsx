import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Profile } from "@/lib/api/profile";

import { ProfileDashboard } from "./ProfileDashboard";

const API = "http://localhost:8000/api/v1/profile";

function makeProfile(overrides: Partial<Profile> = {}): Profile {
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

type Handler = (init: RequestInit | undefined) => { status: number; body?: unknown };
let routes: Record<string, Handler>;
let calls: { key: string; body: unknown }[];

function json(status: number, body?: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url.replace(API, "") || "/"}`;
      calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const handler = routes[key];
      if (!handler) return json(500, { detail: `unmocked ${key}` });
      const { status, body } = handler(init);
      return json(status, body);
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("ProfileDashboard", () => {
  it("offers to create a profile when none exists, then shows it", async () => {
    routes = {
      "GET /": () => ({ status: 404, body: { detail: "Profile not found." } }),
      "POST /": () => ({ status: 201, body: makeProfile({ headline: "ML engineer" }) }),
    };
    render(<ProfileDashboard />);

    const form = await screen.findByRole("heading", { name: /create your master profile/i });
    expect(form).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /create profile/i }));
    expect(await screen.findByText("Full name is required")).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /")).toBe(false);

    fireEvent.change(screen.getByLabelText(/full name/i), {
      target: { value: "  Test Candidate " },
    });
    fireEvent.click(screen.getByRole("button", { name: /create profile/i }));

    expect(await screen.findByText("ML engineer")).toBeInTheDocument();
    const post = calls.find((c) => c.key === "POST /");
    expect(post?.body).toMatchObject({ full_name: "Test Candidate", headline: null });
  });

  it("shows server validation errors next to the field", async () => {
    routes = {
      "GET /": () => ({ status: 404, body: { detail: "Profile not found." } }),
      "POST /": () => ({
        status: 422,
        body: {
          detail: [
            { loc: ["body", "contact_email"], msg: "Value error, must be a valid email address" },
          ],
        },
      }),
    };
    render(<ProfileDashboard />);
    fireEvent.change(await screen.findByLabelText(/full name/i), { target: { value: "A" } });
    fireEvent.change(screen.getByLabelText(/contact email/i), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: /create profile/i }));
    expect(await screen.findByText("must be a valid email address")).toBeInTheDocument();
    expect(screen.getByLabelText(/contact email/i)).toHaveAttribute("aria-invalid", "true");
  });

  it("renders sections with their highlights and deletes an item after confirmation", async () => {
    let profile = makeProfile({
      projects: [
        {
          id: "proj1",
          sort_order: 0,
          title: "Multi-Agent Research Assistant",
          role: "Lead",
          evidence: [
            {
              id: "ev1",
              source_type: "project",
              subject_id: "proj1",
              content: "Implemented RAG pipeline using document retrieval and QA.",
              origin: "user_entered",
              confirmed_at: "2026-01-01T00:00:00Z",
              is_cited: false,
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ],
        },
      ],
    });
    routes = {
      "GET /": () => ({ status: 200, body: profile }),
      "DELETE /projects/proj1": () => {
        profile = makeProfile();
        return { status: 204 };
      },
    };
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    render(<ProfileDashboard />);

    const projects = await screen.findByRole("region", { name: "Projects" });
    expect(within(projects).getByText("Multi-Agent Research Assistant")).toBeInTheDocument();
    expect(within(projects).getByText(/Implemented RAG pipeline/)).toBeInTheDocument();

    fireEvent.click(within(projects).getByRole("button", { name: /delete multi-agent/i }));
    await waitFor(() =>
      expect(within(projects).getByText(/no projects added yet/i)).toBeInTheDocument(),
    );
    expect(calls.map((c) => c.key)).toContain("DELETE /projects/proj1");
  });

  it("keeps AI suggestions separate and applies them only on accept", async () => {
    let pending = 1;
    routes = {
      "GET /": () => ({ status: 200, body: makeProfile({ pending_suggestions: pending }) }),
      "GET /suggestions": () => ({
        status: 200,
        body: [
          {
            id: "s1",
            section: "project",
            action: "create",
            target_id: null,
            proposed_data: { title: "Chatbot" },
            source: "resume_extraction",
            rationale: null,
            status: "pending",
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
      }),
      "POST /suggestions/s1/accept": () => {
        pending = 0;
        return { status: 200, body: {} };
      },
    };
    render(<ProfileDashboard />);

    const card = await screen.findByRole("region", {
      name: /ai suggestions awaiting your review/i,
    });
    expect(within(card).getByText(/not part of your profile/i)).toBeInTheDocument();
    expect(await within(card).findByText("Chatbot")).toBeInTheDocument();
    expect(calls.some((c) => c.key.startsWith("POST /suggestions"))).toBe(false);

    fireEvent.click(within(card).getByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: /ai suggestions/i })).not.toBeInTheDocument(),
    );
    expect(calls.map((c) => c.key)).toContain("POST /suggestions/s1/accept");
  });

  it("shows a helpful error when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<ProfileDashboard />);
    expect(await screen.findByText(/cannot reach the careerpilot api/i)).toBeInTheDocument();
  });
});
