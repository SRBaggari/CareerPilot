import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentRun } from "@/lib/api/agent";
import type { ApplicationSummary, Dashboard } from "@/lib/api/applications";
import type { JobSummary } from "@/lib/api/jobs";

import { makeProfile, stubApi } from "../profile/testApi";
import { dashboardStats, DashboardPage, recentActivity } from "./DashboardPage";

afterEach(() => vi.unstubAllGlobals());

const job = (id: string, title: string, created: string): JobSummary => ({
  id,
  title,
  company_name: "Northwind",
  location: null,
  workplace_type: null,
  employment_type: null,
  application_deadline: null,
  input_method: "pasted_text",
  requirement_counts: { required: 3, preferred: 1, informational: 0 },
  created_at: created,
});

const app = (overrides: Partial<ApplicationSummary>): ApplicationSummary => ({
  id: "a1",
  job_id: "j1",
  company: "Northwind",
  position: "ML Engineer",
  location: null,
  job_url: null,
  status: "awaiting_approval",
  discovered_at: null,
  applied_at: null,
  approved_at: null,
  approval_state: "ready_for_review",
  updated_at: "2026-09-30T12:00:00Z",
  next_interview_at: null,
  next_follow_up_at: null,
  overdue_follow_ups: 0,
  has_resume: true,
  has_cover_letter: true,
  answers_approved: 1,
  answers_total: 1,
  ...overrides,
});

const DASHBOARD: Dashboard = {
  counts: {
    discovered: 1,
    saved: 2,
    analyzed: 0,
    application_prepared: 1,
    awaiting_approval: 1,
    submitted: 3,
    assessment: 1,
    interview: 1,
    offer: 0,
    rejected: 1,
    withdrawn: 0,
  },
  total: 11,
  active: 10,
  follow_ups_due: [
    {
      id: "f1",
      application_id: "a3",
      company: "Ledgerly",
      position: "Backend Engineer",
      interview_id: null,
      channel: "email",
      status: "pending",
      due_at: "2026-09-28T09:00:00Z",
      completed_at: null,
      subject: "Check in on your application",
      notes: null,
      overdue: true,
    },
  ],
  upcoming_interviews: [
    {
      id: "i1",
      application_id: "a3",
      company: "Ledgerly",
      position: "Backend Engineer",
      interview_type: "technical",
      status: "scheduled",
      scheduled_at: "2026-10-03T10:00:00Z",
      duration_minutes: 60,
      location: null,
      meeting_url: null,
      notes: null,
    },
  ],
  recent: [app({})],
};

const RUN = {
  id: "r1",
  stage: "approve",
  status: "waiting_for_human",
  pause: { kind: "approval_required", message: "Approve it.", items: [] },
  updated_at: "2026-09-30T13:00:00Z",
} as unknown as AgentRun;

const JOBS = [
  job("j1", "ML Engineer", "2026-09-29T08:00:00Z"),
  job("j2", "Data Analyst", "2026-09-20T08:00:00Z"),
];

describe("dashboard helpers", () => {
  it("counts each stage of the search", () => {
    const stats = Object.fromEntries(dashboardStats(JOBS, DASHBOARD).map((s) => [s.label, s]));
    expect(stats["Jobs discovered"].value).toBe(2);
    expect(stats["Saved jobs"].value).toBe(2);
    expect(stats["Applications prepared"].value).toBe(2);
    expect(stats["Applications prepared"].hint).toBe("1 awaiting approval");
    expect(stats["Applications submitted"].value).toBe(5); // submitted + assessment + interview
    expect(stats["Interviews"].value).toBe(1);
    expect(stats["Interviews"].hint).toBe("1 upcoming");
    expect(stats["Follow-ups"].value).toBe(1);
    expect(stats["Follow-ups"].hint).toBe("1 overdue");
  });

  it("merges recent activity, newest first", () => {
    const activity = recentActivity(JOBS, DASHBOARD, [RUN], 3);
    expect(activity.map((a) => a.title)).toEqual([
      "Agent waiting for you",
      "ML Engineer: Awaiting approval",
      "Added ML Engineer",
    ]);
    expect(activity[1].href).toBe("/applications/a1");
  });
});

describe("DashboardPage", () => {
  it("shows the summary, what needs attention and recent activity", async () => {
    stubApi({
      "GET /profile": () => ({ status: 200, body: makeProfile({ full_name: "Priya Sharma" }) }),
      "GET /jobs": () => ({ status: 200, body: JOBS }),
      "GET /applications/dashboard": () => ({ status: 200, body: DASHBOARD }),
      "GET /agent/runs": () => ({ status: 200, body: [RUN] }),
    });
    render(<DashboardPage />);
    expect(await screen.findByRole("heading", { name: "Welcome back, Priya" })).toBeVisible();
    const summary = await screen.findByRole("region", { name: "Summary" });
    expect(within(summary).getByRole("link", { name: /Jobs discovered\s*2/ })).toHaveAttribute(
      "href",
      "/jobs",
    );
    expect(within(summary).getByRole("link", { name: /Applications submitted\s*5/ })).toBeVisible();

    const attention = screen.getByRole("region", { name: "Needs your attention" });
    expect(within(attention).getByRole("link", { name: "Review" })).toHaveAttribute(
      "href",
      "/applications/a1/review",
    );
    expect(attention).toHaveTextContent("The agent is waiting for you at Approve");
    expect(attention).toHaveTextContent("Overdue: Check in on your application");

    expect(screen.getByRole("region", { name: "Upcoming interviews" })).toHaveTextContent(
      "Backend Engineer at Ledgerly",
    );
    const activity = screen.getByRole("list", { name: "Recent activity" });
    expect(within(activity).getAllByRole("listitem")).toHaveLength(4);
  });

  it("guides a new user to create a profile", async () => {
    stubApi({ "GET /profile": () => ({ status: 404, body: { detail: "Profile not found." } }) });
    render(<DashboardPage />);
    const start = await screen.findByRole("region", { name: "Get started" });
    expect(within(start).getByRole("link", { name: "Create your profile" })).toHaveAttribute(
      "href",
      "/profile",
    );
    expect(screen.queryByRole("region", { name: "Summary" })).toBeNull();
  });

  it("still shows what loaded when part of it fails", async () => {
    stubApi({
      "GET /profile": () => ({ status: 200, body: makeProfile() }),
      "GET /jobs": () => ({ status: 200, body: JOBS }),
      "GET /applications/dashboard": () => ({ status: 500, body: { detail: "boom" } }),
      "GET /agent/runs": () => ({ status: 200, body: [] }),
    });
    render(<DashboardPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn't be loaded");
    expect(screen.getByRole("link", { name: /Jobs discovered\s*2/ })).toBeVisible();
    expect(screen.getByText("You're all caught up.")).toBeVisible();
  });
});
