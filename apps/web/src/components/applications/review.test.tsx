import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ResumeContent } from "@/lib/api/tailoredResumes";
import type { ReviewPackage, Verification } from "@/lib/api/review";

import { stubApi } from "../profile/testApi";
import { ReviewPage } from "./ReviewPage";

afterEach(() => vi.unstubAllGlobals());

const HASH = "c".repeat(64);
const VERIFIED: Verification = {
  document_status: "verified",
  verified: true,
  outcome: "approved",
  verifier: "rules",
  checked_at: "2026-09-30T10:00:00Z",
  counts: { supported: 4, partially_supported: 0, unsupported: 0, contradicted: 0 },
  claims_verified: 4,
  unverified: [],
};

const RESUME: ResumeContent = {
  header: {
    full_name: "Test Candidate",
    headline: null,
    contact_email: "t@example.test",
    phone: null,
    location: null,
    website_url: null,
    linkedin_url: null,
    github_url: null,
  },
  summary: [{ claim_id: "s1", text: "Machine learning engineer.", evidence_ids: [] }],
  skills: [],
  experience: [],
  projects: [],
  education: [],
  certifications: [],
  achievements: [],
  coursework: [],
};

function pkg(overrides: Partial<ReviewPackage> = {}): ReviewPackage {
  return {
    application_id: "a1",
    status: "awaiting_approval",
    approval_state: "ready_for_review",
    job: {
      id: "j1",
      title: "ML Engineer",
      company: "Northwind",
      location: "Remote",
      url: "https://jobs.example.com/northwind",
    },
    personal: {
      full_name: "Test Candidate",
      email: "t@example.test",
      email_source: "profile",
      phone: null,
      location: "Pune",
      linkedin: null,
    },
    resume: { id: "r1", version: 2, status: "verified", content: RESUME, verification: VERIFIED },
    cover_letter: {
      id: "l1",
      version: 1,
      status: "verified",
      content: {
        job_title: "ML Engineer",
        company_name: "Northwind",
        signature: {
          full_name: "Test Candidate",
          contact_email: null,
          phone: null,
          location: null,
        },
        greeting: "Dear Hiring Team,",
        paragraphs: [
          { sentences: [{ claim_id: "c1", text: "I built a RAG system.", evidence_ids: [] }] },
        ],
        closing: "Sincerely,",
      },
      verification: VERIFIED,
    },
    answers: [
      {
        id: "q1",
        question: "Describe your experience with Python.",
        answer: "I have used Python for three years.",
        status: "approved",
        approved: true,
        verification: VERIFIED,
      },
    ],
    issues: [
      { section: "personal", severity: "warning", message: "No phone number in your profile." },
    ],
    content_hash: HASH,
    approval: null,
    submitted_at: null,
    can_request_review: false,
    can_approve: true,
    can_submit: false,
    submit_blockers: ["You haven't approved this application."],
    history: [],
    events: [
      {
        id: "e1",
        at: "2026-09-30T10:00:00Z",
        actor: "user",
        user: "me@localhost.dev",
        action: "review_opened",
        message: "You opened the review. Opening it doesn't approve anything.",
        detail: {},
      },
    ],
    ...overrides,
  };
}

describe("ReviewPage", () => {
  it("shows everything to review, and opening it approves nothing", async () => {
    const calls = stubApi({ "GET /applications/a1/review": () => ({ status: 200, body: pkg() }) });
    render(<ReviewPage applicationId="a1" />);
    expect(await screen.findByLabelText("Approval state")).toHaveTextContent("Ready for review");
    const job = screen.getByRole("region", { name: "Job and company" });
    expect(job).toHaveTextContent("ML Engineer");
    expect(job).toHaveTextContent("Northwind");
    expect(screen.getByRole("region", { name: "Resume" })).toHaveTextContent("Test Candidate");
    expect(screen.getByRole("region", { name: "Cover letter" })).toHaveTextContent(
      "I built a RAG system.",
    );
    expect(screen.getByRole("region", { name: "Application answers" })).toHaveTextContent(
      "I have used Python for three years.",
    );
    expect(screen.getByRole("region", { name: "Personal information" })).toHaveTextContent(
      "t@example.test",
    );
    expect(screen.getAllByLabelText("Verification result")).toHaveLength(3);
    expect(screen.getAllByLabelText("Verification result")[0]).toHaveTextContent(
      "Every claim is verified",
    );
    expect(screen.getByRole("region", { name: "Missing or uncertain" })).toHaveTextContent(
      "No phone number",
    );
    expect(screen.getByLabelText("Audit events")).toHaveTextContent("doesn't approve anything");
    await waitFor(() => expect(calls.every((c) => c.key.startsWith("GET"))).toBe(true));
  });

  it("approves only with explicit confirmation, for the exact version shown", async () => {
    const calls = stubApi({
      "GET /applications/a1/review": () => ({ status: 200, body: pkg() }),
      "POST /applications/a1/approve": () => ({
        status: 200,
        body: pkg({
          approval_state: "approved",
          can_submit: true,
          submit_blockers: [],
          approval: {
            reviewer: "me@localhost.dev",
            approved_at: "2026-09-30T11:00:00Z",
            content_hash: HASH,
            version: 1,
          },
        }),
      }),
    });
    render(<ReviewPage applicationId="a1" />);
    const approve = await screen.findByRole("button", { name: "Approve application" });
    expect(approve).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(approve).toBeEnabled();
    fireEvent.click(approve);
    expect(await screen.findByText(/Approved by me@localhost.dev/)).toBeVisible();
    expect(calls.find((c) => c.key === "POST /applications/a1/approve")?.body).toEqual({
      content_hash: HASH,
      confirm: true,
    });
    expect(screen.getByRole("button", { name: "Withdraw approval" })).toBeEnabled();
  });

  it("can't approve while something is missing or unverified", async () => {
    stubApi({
      "GET /applications/a1/review": () => ({
        status: 200,
        body: pkg({
          can_approve: false,
          resume: {
            id: "r1",
            version: 2,
            status: "verification_failed",
            content: RESUME,
            verification: {
              ...VERIFIED,
              verified: false,
              outcome: "rejected",
              unverified: [
                { text: "Led a team of 40.", status: "unsupported", reason: "No evidence." },
              ],
            },
          },
          issues: [
            {
              section: "resume",
              severity: "blocker",
              message: "The resume has claims that aren't verified against your evidence.",
            },
          ],
        }),
      }),
    });
    render(<ReviewPage applicationId="a1" />);
    const resume = await screen.findByRole("region", { name: "Resume" });
    expect(within(resume).getByLabelText("Verification result")).toHaveTextContent(
      "“Led a team of 40.” (unsupported): No evidence.",
    );
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Approve application" })).toBeDisabled();
    expect(screen.getByText(/Resolve the items marked/)).toBeVisible();
  });

  it("shows a refusal and the current version when the content changed", async () => {
    let version = 0;
    stubApi({
      "GET /applications/a1/review": () => ({
        status: 200,
        body: pkg({ content_hash: version++ === 0 ? HASH : "d".repeat(64) }),
      }),
      "POST /applications/a1/approve": () => ({
        status: 409,
        body: { detail: "The application changed since you opened the review." },
      }),
    });
    render(<ReviewPage applicationId="a1" />);
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Approve application" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("changed since you opened");
    await waitFor(() => expect(version).toBe(2)); // reloaded the current version
  });

  it("marks a draft ready for review", async () => {
    const calls = stubApi({
      "GET /applications/a1/review": () => ({
        status: 200,
        body: pkg({ approval_state: "draft", can_request_review: true, can_approve: false }),
      }),
      "POST /applications/a1/review/request": () => ({ status: 200, body: pkg() }),
    });
    render(<ReviewPage applicationId="a1" />);
    expect(screen.queryByRole("button", { name: "Approve application" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Mark ready for review" }));
    expect(await screen.findByRole("button", { name: "Approve application" })).toBeDisabled();
    expect(calls.some((c) => c.key === "POST /applications/a1/review/request")).toBe(true);
  });
});
