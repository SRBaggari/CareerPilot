import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  filterQuery,
  type Application,
  type ApplicationSummary,
  type Dashboard,
} from "@/lib/api/applications";

import { stubApi } from "../profile/testApi";
import { ApplicationDetailPage } from "./ApplicationDetailPage";
import { ApplicationsPage } from "./ApplicationsPage";

afterEach(() => vi.unstubAllGlobals());

function summary(overrides: Partial<ApplicationSummary> = {}): ApplicationSummary {
  return {
    id: "a1",
    job_id: "j1",
    company: "Northwind",
    position: "ML Engineer",
    location: "Remote",
    job_url: "https://jobs.example.com/northwind",
    status: "application_prepared",
    discovered_at: "2026-09-20T10:00:00Z",
    applied_at: null,
    approved_at: null,
    approval_state: "draft",
    updated_at: "2026-09-30T10:00:00Z",
    next_interview_at: null,
    next_follow_up_at: null,
    overdue_follow_ups: 0,
    has_resume: true,
    has_cover_letter: false,
    answers_approved: 1,
    answers_total: 1,
    ...overrides,
  };
}

const counts = {
  discovered: 0,
  saved: 1,
  analyzed: 0,
  application_prepared: 1,
  awaiting_approval: 0,
  submitted: 0,
  assessment: 0,
  interview: 1,
  offer: 0,
  rejected: 0,
  withdrawn: 0,
};

function dashboard(overrides: Partial<Dashboard> = {}): Dashboard {
  return {
    counts,
    total: 3,
    active: 3,
    follow_ups_due: [
      {
        id: "f1",
        application_id: "a3",
        company: "Ledgerly",
        position: "Backend Engineer",
        interview_id: null,
        channel: "email",
        status: "pending",
        due_at: "2026-09-29T09:00:00Z",
        completed_at: null,
        subject: "Email the recruiter",
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
    recent: [],
    ...overrides,
  };
}

const APPS = [
  summary(),
  summary({ id: "a2", company: "Bluefin Retail", position: "Data Analyst", status: "saved" }),
  summary({
    id: "a3",
    company: "Ledgerly",
    position: "Backend Engineer",
    status: "interview",
    applied_at: "2026-09-21T10:00:00Z",
    overdue_follow_ups: 1,
    next_interview_at: "2026-10-03T10:00:00Z",
  }),
];

describe("filterQuery", () => {
  it("encodes status, search, due follow-ups and sort", () => {
    expect(
      filterQuery({
        statuses: ["saved", "interview"],
        q: " ledger ",
        followUpDue: true,
        sort: "company",
      }),
    ).toBe("status=saved&status=interview&q=ledger&follow_up_due=true&sort=company");
  });
});

describe("ApplicationsPage", () => {
  it("shows the dashboard, reminders and a board with a column per status", async () => {
    stubApi({
      "GET /applications/dashboard": () => ({ status: 200, body: dashboard() }),
      "GET /applications?sort=updated": () => ({ status: 200, body: APPS }),
    });
    render(<ApplicationsPage />);
    const board = await screen.findByLabelText("Status board");
    expect(within(board).getByRole("region", { name: "Saved" })).toHaveTextContent("Data Analyst");
    const interview = within(board).getByRole("region", { name: "Interview" });
    expect(interview).toHaveTextContent("Backend Engineer");
    expect(interview).toHaveTextContent("Follow-up overdue");
    expect(within(board).getAllByRole("region")).toHaveLength(11);
    expect(screen.getByLabelText("Summary")).toHaveTextContent("3Tracked");
    expect(screen.getByLabelText("Follow-ups due")).toHaveTextContent(
      "Overdue Email the recruiter",
    );
    expect(screen.getByLabelText("Upcoming interviews")).toHaveTextContent(
      "Ledgerly: Backend Engineer",
    );
  });

  it("filters, searches and switches to the list view", async () => {
    const calls = stubApi({
      "GET /applications/dashboard": () => ({ status: 200, body: dashboard() }),
      "GET /applications?sort=updated": () => ({ status: 200, body: APPS }),
      "GET /applications?status=interview&q=ledger&follow_up_due=true&sort=updated": () => ({
        status: 200,
        body: [APPS[2]],
      }),
    });
    render(<ApplicationsPage />);
    await screen.findByLabelText("Status board");
    fireEvent.click(screen.getByRole("button", { name: "Interview 1" }));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ledger" } });
    fireEvent.click(screen.getByLabelText("Only with a follow-up due this week"));
    await waitFor(() =>
      expect(calls.map((c) => c.key)).toContain(
        "GET /applications?status=interview&q=ledger&follow_up_due=true&sort=updated",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    const table = await screen.findByRole("table", { name: "Applications" });
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
    expect(table).toHaveTextContent("Backend Engineer");
  });

  it("moves an application on the board and explains a refused move", async () => {
    const calls = stubApi({
      "GET /applications/dashboard": () => ({ status: 200, body: dashboard() }),
      "GET /applications?sort=updated": () => ({ status: 200, body: APPS }),
      "POST /applications/a1/status": () => ({
        status: 409,
        body: {
          detail:
            "Can't move to Submitted. Approve the application first: nothing counts as submitted without your approval.",
        },
      }),
    });
    render(<ApplicationsPage />);
    await screen.findByLabelText("Status board");
    const select = screen.getByLabelText("Move ML Engineer at Northwind to");
    fireEvent.change(select, { target: { value: "submitted" } });
    // Choosing alone moves nothing (keyboard users step through the options).
    expect(calls.some((c) => c.key === "POST /applications/a1/status")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Move ML Engineer at Northwind" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Approve the application first/);
    expect(calls.find((c) => c.key === "POST /applications/a1/status")?.body).toEqual({
      status: "submitted",
    });
  });

  it("asks before moving an application to an ending status", async () => {
    const calls = stubApi({
      "GET /applications/dashboard": () => ({ status: 200, body: dashboard() }),
      "GET /applications?sort=updated": () => ({ status: 200, body: APPS }),
      "POST /applications/a1/status": () => ({ status: 200, body: {} }),
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ApplicationsPage />);
    await screen.findByLabelText("Status board");
    fireEvent.change(screen.getByLabelText("Move ML Engineer at Northwind to"), {
      target: { value: "withdrawn" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Move ML Engineer at Northwind" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Withdrawn"));
    expect(calls.some((c) => c.key === "POST /applications/a1/status")).toBe(false);
    confirm.mockRestore();
  });

  it("completes a follow-up reminder from the dashboard", async () => {
    const calls = stubApi({
      "GET /applications/dashboard": () => ({ status: 200, body: dashboard() }),
      "GET /applications?sort=updated": () => ({ status: 200, body: APPS }),
      "PATCH /applications/a3/follow-ups/f1": () => ({ status: 200, body: {} }),
    });
    render(<ApplicationsPage />);
    const due = await screen.findByLabelText("Follow-ups due");
    fireEvent.click(within(due).getByRole("button", { name: "Done" }));
    await waitFor(() =>
      expect(calls.find((c) => c.key === "PATCH /applications/a3/follow-ups/f1")?.body).toEqual({
        status: "done",
      }),
    );
  });
});

function detail(overrides: Partial<Application> = {}): Application {
  return {
    ...summary(),
    notes: "Referral from Asha.",
    resume: {
      id: "r1",
      version: 2,
      status: "verified",
      created_at: "2026-09-25T10:00:00Z",
      newer_version: 3,
    },
    cover_letter: null,
    answers: [{ id: "q1", question: "Why us?", status: "approved", approved: true }],
    readiness: [
      { label: "Tailored resume", ok: true, detail: "Version 2, verified.", required: true },
      { label: "Cover letter", ok: true, detail: "None attached (optional).", required: false },
      { label: "Application answers", ok: true, detail: "1 of 1 approved.", required: true },
    ],
    approval_blockers: [],
    allowed_statuses: ["discovered", "saved", "analyzed", "awaiting_approval", "withdrawn"],
    interviews: [],
    follow_ups: [],
    timeline: [
      {
        at: "2026-09-26T10:00:00Z",
        kind: "status",
        title: "Moved to Application prepared",
        detail: null,
        upcoming: false,
      },
      {
        at: "2026-09-25T10:00:00Z",
        kind: "document",
        title: "Tailored resume v2 created",
        detail: null,
        upcoming: false,
      },
      {
        at: "2026-09-20T10:00:00Z",
        kind: "created",
        title: "Started tracking",
        detail: "ML Engineer at Northwind",
        upcoming: false,
      },
    ],
    ...overrides,
  };
}

describe("ApplicationDetailPage", () => {
  it("shows the tracked facts, documents, readiness and timeline", async () => {
    stubApi({ "GET /applications/a1": () => ({ status: 200, body: detail() }) });
    render(<ApplicationDetailPage applicationId="a1" />);
    expect(await screen.findByLabelText("Status")).toHaveTextContent("Application prepared");
    const facts = screen.getByLabelText("Application details");
    expect(within(facts).getByRole("link", { name: "Open posting" })).toHaveAttribute(
      "href",
      "https://jobs.example.com/northwind",
    );
    expect(facts).toHaveTextContent("Discovered");
    expect(screen.getByText(/version 3 is newer/)).toBeVisible();
    expect(screen.getByLabelText("Answers")).toHaveTextContent("Why us? — approved");
    expect(screen.getByLabelText("Readiness")).toHaveTextContent(
      "Tailored resume: Version 2, verified.",
    );
    expect(screen.getByLabelText("Timeline")).toHaveTextContent("Started tracking");
    expect(screen.getByLabelText("Notes")).toHaveValue("Referral from Asha.");
    expect(screen.getByText(/Interviews can be added once/)).toBeVisible();
  });

  it("links approved applications to the review, then records the submission", async () => {
    const calls = stubApi({
      "GET /applications/a1": () => ({
        status: 200,
        body: detail({
          status: "awaiting_approval",
          approval_state: "approved",
          approved_at: "2026-09-30T10:00:00Z",
          approval_blockers: ["Already approved."],
          allowed_statuses: ["saved", "application_prepared", "submitted", "withdrawn"],
        }),
      }),
      "POST /applications/a1/status": () => ({
        status: 200,
        body: detail({
          status: "submitted",
          approved_at: "2026-09-30T10:00:00Z",
          applied_at: "2026-09-30T12:00:00Z",
          allowed_statuses: ["assessment", "interview", "offer", "rejected", "withdrawn"],
        }),
      }),
    });
    render(<ApplicationDetailPage applicationId="a1" />);
    expect(await screen.findByText(/Approved by you on/)).toBeVisible();
    expect(screen.getByLabelText("Approval state")).toHaveTextContent("Approved");
    expect(screen.getByRole("link", { name: "Open the review" })).toHaveAttribute(
      "href",
      "/applications/a1/review",
    );
    expect(screen.queryByRole("button", { name: "Approve application" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Move to"), { target: { value: "submitted" } });
    fireEvent.change(screen.getByLabelText("Date you applied"), {
      target: { value: "2026-09-30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update status" }));
    expect(await screen.findByLabelText("Status")).toHaveTextContent("Submitted");
    expect(calls.find((c) => c.key === "POST /applications/a1/status")?.body).toEqual({
      status: "submitted",
      submitted_on: "2026-09-30",
    });
  });

  it("explains what blocks approval", async () => {
    stubApi({
      "GET /applications/a1": () => ({
        status: 200,
        body: detail({
          approval_blockers: ["Approve every application answer (0 of 1 approved)."],
        }),
      }),
    });
    render(<ApplicationDetailPage applicationId="a1" />);
    expect(await screen.findByLabelText("Approval blockers")).toHaveTextContent("0 of 1 approved");
    // Approval only happens on the review page, never with a single click here.
    expect(screen.queryByRole("button", { name: "Approve application" })).toBeNull();
    expect(screen.getByRole("link", { name: "Review everything and approve" })).toBeVisible();
  });

  it("adds interviews and follow-ups, and saves notes", async () => {
    const submitted = detail({
      status: "submitted",
      applied_at: "2026-09-30T12:00:00Z",
      approved_at: "2026-09-30T10:00:00Z",
    });
    const calls = stubApi({
      "GET /applications/a1": () => ({ status: 200, body: submitted }),
      "POST /applications/a1/interviews": () => ({
        status: 200,
        body: {
          ...submitted,
          status: "interview",
          interviews: [
            {
              id: "i1",
              application_id: "a1",
              company: "Northwind",
              position: "ML Engineer",
              interview_type: "technical",
              status: "scheduled",
              scheduled_at: "2026-10-05T10:00:00Z",
              duration_minutes: null,
              location: "Video call",
              meeting_url: null,
              notes: null,
            },
          ],
        },
      }),
      "POST /applications/a1/follow-ups": () => ({
        status: 200,
        body: {
          ...submitted,
          follow_ups: [
            {
              id: "f1",
              application_id: "a1",
              company: "Northwind",
              position: "ML Engineer",
              interview_id: null,
              channel: "email",
              status: "pending",
              due_at: "2026-10-07T00:00:00Z",
              completed_at: null,
              subject: "Ask about next steps",
              notes: null,
              overdue: false,
            },
          ],
        },
      }),
      "PATCH /applications/a1": () => ({ status: 200, body: { ...submitted, notes: "Updated." } }),
    });
    render(<ApplicationDetailPage applicationId="a1" />);
    const interviewForm = await screen.findByRole("form", { name: "Add interview" });
    fireEvent.change(within(interviewForm).getByLabelText("Type"), {
      target: { value: "technical" },
    });
    fireEvent.change(within(interviewForm).getByLabelText("Where (optional)"), {
      target: { value: "Video call" },
    });
    fireEvent.click(within(interviewForm).getByRole("button", { name: "Add interview" }));
    expect(await screen.findByLabelText("Interviews")).toHaveTextContent("technical");

    const followForm = screen.getByRole("form", { name: "Add follow-up" });
    fireEvent.change(within(followForm).getByLabelText("Reminder"), {
      target: { value: "Ask about next steps" },
    });
    fireEvent.change(within(followForm).getByLabelText("Due"), { target: { value: "2026-10-07" } });
    fireEvent.click(within(followForm).getByRole("button", { name: "Add follow-up" }));
    expect(await screen.findByLabelText("Follow-ups")).toHaveTextContent("Ask about next steps");
    // Due at the end of the chosen day in the user's time zone, not UTC midnight (which
    // shows as the day before anywhere west of UTC).
    const due = (
      calls.find((c) => c.key === "POST /applications/a1/follow-ups")?.body as {
        due_at: string;
      }
    ).due_at;
    expect(new Date(due).getDate()).toBe(7);
    expect(new Date(due).getHours()).toBe(23);

    fireEvent.change(screen.getByLabelText("Notes"), { target: { value: "Updated." } });
    fireEvent.click(screen.getByRole("button", { name: "Save notes" }));
    await waitFor(() =>
      expect(calls.find((c) => c.key === "PATCH /applications/a1")?.body).toEqual({
        notes: "Updated.",
      }),
    );
  });
});
