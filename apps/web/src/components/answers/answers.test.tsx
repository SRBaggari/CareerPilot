import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApplicationAnswer } from "@/lib/api/applicationAnswers";

import { stubApi } from "../profile/testApi";
import { ApplicationQuestionsPage } from "./ApplicationQuestionsPage";

afterEach(() => vi.unstubAllGlobals());

const JOB_ID = "j1";
const LIST = `GET /jobs/${JOB_ID}/application-answers`;
const CREATE = `POST /jobs/${JOB_ID}/application-answers`;
const PYTHON =
  "In my Multi-Agent Research Assistant project, I built data pipelines in Python and SQL.";

function makeAnswer(overrides: Partial<ApplicationAnswer> = {}): ApplicationAnswer {
  return {
    id: "a1",
    job_id: JOB_ID,
    question: "Describe your experience with Python.",
    question_type: "skill",
    focus: "Python",
    understanding: "Asks about your experience with Python.",
    position: 1,
    max_words: null,
    status: "verified",
    approved_at: null,
    generator: "rules",
    created_at: "2026-09-30T10:00:00Z",
    updated_at: "2026-09-30T10:00:00Z",
    sentences: [{ claim_id: "c1", text: PYTHON, evidence_ids: ["e1"] }],
    text: PYTHON,
    word_count: 15,
    evidence_used: [
      {
        evidence_id: "e1",
        content: "Built data pipelines in Python and SQL.",
        record_label: "Multi-Agent Research Assistant",
      },
    ],
    changes: { kept: 1, regenerated: 0, removed: 0, audit: [] },
    notes: [],
    report: {
      id: "r1",
      document_type: "application_answer",
      document_id: "a1",
      trigger: "generation",
      created_at: "2026-09-30T10:00:00Z",
      verifier: "rules",
      outcome: "approved",
      counts: { supported: 1, partially_supported: 0, unsupported: 0, contradicted: 0 },
      claims: [
        {
          claim_text: PYTHON,
          claim_type: "answer",
          section: "answer",
          position: 0,
          record_id: null,
          cited_evidence_ids: ["e1"],
          evidence_ids: ["e1"],
          evidence_source: "cited",
          verification_status: "supported",
          confidence: 1,
          reason: "Supported by the evidence.",
          method: "rule_based",
        },
      ],
      warnings: [],
    },
    ...overrides,
  };
}

const card = (question: string) => screen.getByRole("region", { name: question });

describe("ApplicationQuestionsPage", () => {
  it("shows question, answer, evidence used and verification status for each question", async () => {
    stubApi({ [LIST]: () => ({ status: 200, body: [makeAnswer()] }) });
    render(<ApplicationQuestionsPage jobId={JOB_ID} />);
    const region = await screen.findByRole("region", {
      name: "Describe your experience with Python.",
    });
    expect(within(region).getByText("Asks about your experience with Python.")).toBeVisible();
    expect(within(region).getByText(PYTHON)).toBeVisible();
    const evidence = within(region).getByRole("list", { name: "Evidence used" });
    expect(evidence).toHaveTextContent("“Built data pipelines in Python and SQL.”");
    expect(evidence).toHaveTextContent("Multi-Agent Research Assistant");
    expect(within(region).getByLabelText("Verification status")).toHaveTextContent("Verified");
    for (const name of ["Edit", "Regenerate", "Approve"])
      expect(within(region).getByRole("button", { name })).toBeEnabled();
  });

  it("generates answers for several questions at once", async () => {
    const calls = stubApi({
      [LIST]: () => ({ status: 200, body: [] }),
      [CREATE]: () => ({
        status: 201,
        body: [
          makeAnswer(),
          makeAnswer({
            id: "a2",
            question: "What is your experience with Kubernetes?",
            focus: "Kubernetes",
            understanding: "Asks about your experience with Kubernetes.",
            status: "draft",
            sentences: [],
            text: "",
            word_count: 0,
            evidence_used: [],
            report: null,
            notes: ["Your verified evidence doesn't show experience with Kubernetes."],
          }),
        ],
      }),
    });
    render(<ApplicationQuestionsPage jobId={JOB_ID} />);
    await screen.findByText("No questions yet.");
    fireEvent.change(screen.getByLabelText("Questions"), {
      target: {
        value: "Describe your experience with Python.\n\nWhat is your experience with Kubernetes?",
      },
    });
    fireEvent.change(screen.getByLabelText("Word limit (optional)"), { target: { value: "120" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate 2 answers" }));

    const none = await screen.findByRole("region", {
      name: "What is your experience with Kubernetes?",
    });
    expect(within(none).getByLabelText("Verification status")).toHaveTextContent("No answer yet");
    expect(within(none).getByText(/doesn't show experience with Kubernetes/)).toBeVisible();
    expect(within(none).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(calls.find((c) => c.key === CREATE)?.body).toEqual({
      questions: [
        "Describe your experience with Python.",
        "What is your experience with Kubernetes?",
      ],
      max_words: 120,
    });
  });

  it("edits an answer and shows why an unsupported edit was rejected", async () => {
    const calls = stubApi({
      [LIST]: () => ({ status: 200, body: [makeAnswer()] }),
      "PUT /application-answers/a1": (body) =>
        (body as { answer: string }).answer.includes("5 years")
          ? {
              status: 422,
              body: {
                detail: [
                  {
                    type: "value_error",
                    loc: ["body", "answer"],
                    msg: "Unsupported: “I have 5 years of Python”. Contains numbers or metrics not in the evidence: 5.",
                  },
                ],
              },
            }
          : {
              status: 200,
              body: makeAnswer({ text: "I built data pipelines in Python and SQL." }),
            },
    });
    render(<ApplicationQuestionsPage jobId={JOB_ID} />);
    const region = await screen.findByRole("region", {
      name: "Describe your experience with Python.",
    });
    fireEvent.click(within(region).getByRole("button", { name: "Edit" }));
    const box = within(region).getByLabelText("Answer to: Describe your experience with Python.");
    fireEvent.change(box, { target: { value: "I have 5 years of Python." } });
    fireEvent.click(within(region).getByRole("button", { name: "Save" }));
    expect(await within(region).findByRole("alert")).toHaveTextContent(/not in the evidence: 5/);

    fireEvent.change(box, { target: { value: "I built data pipelines in Python and SQL." } });
    fireEvent.click(within(region).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        within(card("Describe your experience with Python.")).getByText(
          "I built data pipelines in Python and SQL.",
        ),
      ).toBeVisible(),
    );
    expect(calls.filter((c) => c.key === "PUT /application-answers/a1")).toHaveLength(2);
  });

  it("approves an answer", async () => {
    stubApi({
      [LIST]: () => ({ status: 200, body: [makeAnswer()] }),
      "POST /application-answers/a1/approve": () => ({
        status: 200,
        body: makeAnswer({ status: "approved", approved_at: "2026-09-30T11:00:00Z" }),
      }),
    });
    render(<ApplicationQuestionsPage jobId={JOB_ID} />);
    const region = await screen.findByRole("region", {
      name: "Describe your experience with Python.",
    });
    fireEvent.click(within(region).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(within(region).getByLabelText("Verification status")).toHaveTextContent("Approved"),
    );
    expect(within(region).getByRole("button", { name: "Approved" })).toBeDisabled();
  });

  it("explains when approval is refused", async () => {
    stubApi({
      [LIST]: () => ({ status: 200, body: [makeAnswer()] }),
      "POST /application-answers/a1/approve": () => ({
        status: 409,
        body: {
          detail: "This answer no longer passes verification against your current evidence.",
        },
      }),
    });
    render(<ApplicationQuestionsPage jobId={JOB_ID} />);
    const region = await screen.findByRole("region", {
      name: "Describe your experience with Python.",
    });
    fireEvent.click(within(region).getByRole("button", { name: "Approve" }));
    expect(await within(region).findByRole("alert")).toHaveTextContent(/no longer passes/);
  });

  it("regenerates an answer", async () => {
    const calls = stubApi({
      [LIST]: () => ({ status: 200, body: [makeAnswer()] }),
      "POST /application-answers/a1/regenerate": () => ({
        status: 200,
        body: makeAnswer({ text: "I built data pipelines in Python and SQL." }),
      }),
    });
    render(<ApplicationQuestionsPage jobId={JOB_ID} />);
    const region = await screen.findByRole("region", {
      name: "Describe your experience with Python.",
    });
    fireEvent.click(within(region).getByRole("button", { name: "Regenerate" }));
    expect(
      await within(region).findByText("I built data pipelines in Python and SQL."),
    ).toBeVisible();
    expect(calls.map((c) => c.key)).toContain("POST /application-answers/a1/regenerate");
  });
});
