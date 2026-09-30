import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Application } from "@/lib/api/applications";
import type { AssistedRun, Review } from "@/lib/api/assist";

import { stubApi } from "../profile/testApi";
import { AssistPage } from "./AssistPage";

afterEach(() => vi.unstubAllGlobals());

const HASH = "a".repeat(64);

function application(overrides: Partial<Application> = {}): Application {
  return {
    id: "a1",
    position: "ML Engineer",
    company: "Northwind",
    approved_at: "2026-09-30T09:00:00Z",
    applied_at: null,
    ...overrides,
  } as Application;
}

const REVIEW: Review = {
  destination: {
    url: "http://127.0.0.1:8790/jobs/northwind/apply",
    host: "127.0.0.1:8790",
    adapter: "mock-site",
    form_action: "http://127.0.0.1:8790/jobs/northwind/submit",
  },
  personal: {
    first_name: "Test",
    last_name: "Candidate",
    email: "t@example.test",
    phone: null,
    location: "Pune",
    linkedin: null,
  },
  resume: {
    field_label: "Resume (PDF)",
    document: "resume",
    version: 2,
    file_name: "Test-Candidate-resume-v2.pdf",
    size_bytes: 2048,
    sha256: "b".repeat(64),
  },
  cover_letter: null,
  answers: [
    {
      field_id: "q1",
      question: "Why are you interested in this role?",
      answer: "I built a RAG system.",
      answer_id: "ans1",
    },
  ],
  additional_fields: [
    {
      field_id: "work_authorization",
      label: "Are you authorized to work in India?",
      value: "Yes",
      source: "you provided this",
    },
  ],
  left_blank: ["How did you hear about us?"],
};

function run(overrides: Partial<AssistedRun> = {}): AssistedRun {
  return {
    id: "r1",
    application_id: "a1",
    status: "awaiting_review",
    destination_url: REVIEW.destination.url,
    destination_host: REVIEW.destination.host,
    adapter: "mock-site",
    inputs: { work_authorization: "Yes" },
    review: REVIEW,
    review_hash: HASH,
    problems: [],
    stop_reason: null,
    prepared_at: "2026-09-30T10:00:00Z",
    confirmed_at: null,
    submitted_at: null,
    confirmation_reference: null,
    nothing_submitted: true,
    created_at: "2026-09-30T10:00:00Z",
    updated_at: "2026-09-30T10:00:00Z",
    events: [
      {
        id: "e1",
        at: "2026-09-30T10:00:00Z",
        actor: "user",
        action: "started",
        message: "You asked CareerPilot to fill this application for your review.",
        detail: {},
      },
      {
        id: "e2",
        at: "2026-09-30T10:00:01Z",
        actor: "automation",
        action: "paused_for_review",
        message: "Paused before submitting. Nothing has been submitted.",
        detail: {},
      },
    ],
    ...overrides,
  };
}

describe("AssistPage", () => {
  it("shows exactly what will be submitted and requires explicit confirmation", async () => {
    const calls = stubApi({
      "GET /applications/a1": () => ({ status: 200, body: application() }),
      "GET /applications/a1/assisted-runs": () => ({ status: 200, body: [run()] }),
      "POST /assisted-runs/r1/submit": () => ({
        status: 200,
        body: run({
          status: "submitted",
          review: REVIEW,
          confirmed_at: "2026-09-30T10:05:00Z",
          submitted_at: "2026-09-30T10:05:02Z",
          confirmation_reference: "MOCK-1001",
          nothing_submitted: false,
        }),
      }),
    });
    render(<AssistPage applicationId="a1" />);

    expect(await screen.findByText(/Nothing has been submitted yet/)).toBeInTheDocument();
    for (const section of [
      "Destination website",
      "Personal information",
      "Resume",
      "Cover letter",
      "Application answers",
      "Additional fields",
    ]) {
      expect(screen.getByRole("region", { name: section })).toBeInTheDocument();
    }
    const personal = screen.getByRole("region", { name: "Personal information" });
    expect(personal).toHaveTextContent("Test");
    expect(personal).toHaveTextContent("t@example.test");
    expect(screen.getByLabelText("Resume file")).toHaveTextContent("Test-Candidate-resume-v2.pdf");
    expect(screen.getByRole("region", { name: "Cover letter" })).toHaveTextContent(
      "No cover letter will be sent.",
    );
    expect(screen.getByRole("region", { name: "Application answers" })).toHaveTextContent(
      "I built a RAG system.",
    );
    expect(screen.getByRole("region", { name: "Additional fields" })).toHaveTextContent(
      "Yes (you provided this)",
    );
    expect(screen.getByRole("region", { name: "Destination website" })).toHaveTextContent(
      "127.0.0.1:8790/jobs/northwind/submit",
    );
    expect(screen.getByLabelText("Audit events")).toHaveTextContent("Paused before submitting.");

    // Submit stays disabled until the explicit confirmation is ticked.
    const submit = screen.getByRole("button", { name: "Submit application" });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(calls.some((c) => c.key.includes("/submit"))).toBe(false);

    fireEvent.click(screen.getByRole("checkbox"));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    expect(await screen.findByText("Submitted after your confirmation.")).toBeInTheDocument();
    const sent = calls.find((c) => c.key === "POST /assisted-runs/r1/submit");
    expect(sent?.body).toEqual({ review_hash: HASH, confirm: true });
    expect(screen.getByText(/reference MOCK-1001/)).toBeInTheDocument();
  });

  it("asks for what it doesn't have and never guesses", async () => {
    const needs = run({
      status: "needs_input",
      review: null,
      review_hash: null,
      inputs: {},
      problems: [
        {
          field_id: "work_authorization",
          label: "Are you authorized to work in India?",
          kind: "select",
          required: true,
          options: ["Yes", "No"],
          message: "CareerPilot doesn't have this in your records and won't guess: provide it.",
          needs_input: true,
        },
        {
          field_id: "q3",
          label: "What is your notice period?",
          kind: "textarea",
          required: true,
          options: [],
          message: "No approved answer to this question.",
          needs_input: false,
        },
      ],
    });
    const calls = stubApi({
      "GET /applications/a1": () => ({ status: 200, body: application() }),
      "GET /applications/a1/assisted-runs": () => ({ status: 200, body: [needs] }),
      "POST /assisted-runs/r1/inputs": () => ({ status: 200, body: run() }),
    });
    render(<AssistPage applicationId="a1" />);
    const card = await screen.findByRole("region", { name: "CareerPilot needs you" });
    expect(card).toHaveTextContent("What is your notice period?: No approved answer");
    expect(screen.queryByRole("button", { name: "Submit application" })).toBeNull();
    fireEvent.change(within(card).getByLabelText(/Are you authorized/), {
      target: { value: "No" },
    });
    fireEvent.click(within(card).getByRole("button", { name: "Continue" }));
    await screen.findByText(/Nothing has been submitted yet/);
    expect(calls.find((c) => c.key === "POST /assisted-runs/r1/inputs")?.body).toEqual({
      inputs: { work_authorization: "No" },
    });
  });

  it("explains why it stopped instead of working around the site", async () => {
    stubApi({
      "GET /applications/a1": () => ({ status: 200, body: application() }),
      "GET /applications/a1/assisted-runs": () => ({
        status: 200,
        body: [
          run({
            status: "stopped",
            review: null,
            review_hash: null,
            stop_reason: "The page shows a CAPTCHA. CareerPilot doesn't solve or evade CAPTCHAs.",
          }),
        ],
      }),
    });
    render(<AssistPage applicationId="a1" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("CareerPilot stopped.");
    expect(alert).toHaveTextContent("doesn't solve or evade CAPTCHAs");
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Submit application" })).toBeNull();
  });

  it("only starts for approved applications", async () => {
    const calls = stubApi({
      "GET /applications/a1": () => ({
        status: 200,
        body: application({ approved_at: null }),
      }),
      "GET /applications/a1/assisted-runs": () => ({ status: 200, body: [] }),
      "POST /applications/a1/assisted-runs": () => ({ status: 201, body: run() }),
    });
    render(<AssistPage applicationId="a1" />);
    expect(await screen.findByText(/Approve the application first/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Fill the application/ })).toBeNull();
    await waitFor(() => expect(calls.some((c) => c.key.startsWith("POST"))).toBe(false));
  });

  it("starts filling on request", async () => {
    const calls = stubApi({
      "GET /applications/a1": () => ({ status: 200, body: application() }),
      "GET /applications/a1/assisted-runs": () => ({ status: 200, body: [] }),
      "POST /applications/a1/assisted-runs": () => ({ status: 201, body: run() }),
    });
    render(<AssistPage applicationId="a1" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Fill the application for my review" }),
    );
    await screen.findByText(/Nothing has been submitted yet/);
    expect(calls.find((c) => c.key === "POST /applications/a1/assisted-runs")?.body).toEqual({
      inputs: {},
    });
  });
});
