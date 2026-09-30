import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CoverLetter } from "@/lib/api/coverLetters";

import { stubApi } from "../profile/testApi";
import { CoverLetterPage } from "./CoverLetterPage";

afterEach(() => vi.unstubAllGlobals());

const JOB_ID = "j1";
const LATEST = `GET /jobs/${JOB_ID}/cover-letters/latest`;
const GENERATE = `POST /jobs/${JOB_ID}/cover-letters`;
const SAVE = "PUT /cover-letters/l1";
const OPENING = "I am writing to apply for the ML Engineer position at Northwind.";
const DOCKER =
  "As a Machine Learning Intern at Acme Analytics, I deployed ML models with Docker on AWS.";
const CLOSING = "Thank you for your time and consideration.";

function makeLetter(overrides: Partial<CoverLetter> = {}): CoverLetter {
  return {
    id: "l1",
    job_id: JOB_ID,
    job_title: "ML Engineer",
    company_name: "Northwind",
    version: 1,
    status: "verified",
    generator: "llm:claude-opus-5-5",
    created_at: "2026-09-30T10:00:00Z",
    updated_at: "2026-09-30T10:00:00Z",
    content: {
      job_title: "ML Engineer",
      company_name: "Northwind",
      signature: {
        full_name: "Test Candidate",
        contact_email: "t@example.test",
        phone: null,
        location: null,
      },
      greeting: "Dear Northwind Hiring Team,",
      paragraphs: [
        { sentences: [{ claim_id: "c1", text: OPENING, evidence_ids: [] }] },
        { sentences: [{ claim_id: "c2", text: DOCKER, evidence_ids: ["e1"] }] },
        { sentences: [{ claim_id: "c3", text: CLOSING, evidence_ids: [] }] },
      ],
      closing: "Sincerely,",
    },
    word_count: 42,
    changes: {
      kept: 3,
      regenerated: 1,
      removed: 1,
      audit: [
        {
          section: "Paragraph 2",
          original_text: "At Acme Analytics, I reduced latency by 60% using ONNX.",
          final_text: "At Acme Analytics, I reduced model inference latency by 35% using ONNX.",
          outcome: "regenerated",
          verdict: "contradicted",
          reason: "Your evidence says 35%, not 60%.",
        },
        {
          section: "Paragraph 1",
          original_text: "I am a passionate, detail-oriented team player.",
          final_text: null,
          outcome: "removed",
          verdict: "unsupported",
          reason: "Presents a generic quality as fact (detail-oriented, passionate).",
        },
      ],
    },
    notes: ["Not mentioned, because your evidence doesn't show them: Kubernetes."],
    evidence: {
      e1: {
        content: "Deployed ML models with Docker on AWS.",
        record_label: "Machine Learning Intern at Acme Analytics",
      },
    },
    report: {
      id: "r1",
      document_type: "cover_letter",
      document_id: "l1",
      trigger: "generation",
      created_at: "2026-09-30T10:00:00Z",
      verifier: "rules",
      outcome: "approved",
      counts: { supported: 2, partially_supported: 0, unsupported: 0, contradicted: 0 },
      claims: [
        {
          claim_text: OPENING,
          claim_type: "letter",
          section: "paragraphs:0",
          position: 0,
          record_id: null,
          cited_evidence_ids: [],
          evidence_ids: [],
          evidence_source: "none",
          verification_status: "supported",
          confidence: 1,
          reason: "Not a factual claim about you: a greeting, statement of interest or courtesy.",
          method: "rule_based",
        },
        {
          claim_text: DOCKER,
          claim_type: "letter",
          section: "paragraphs:1",
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

describe("CoverLetterPage", () => {
  it("offers to generate when there is no letter yet", async () => {
    const calls = stubApi({
      [LATEST]: () => ({ status: 404, body: { detail: "No cover letter for this job yet." } }),
      [GENERATE]: () => ({ status: 201, body: makeLetter() }),
    });
    render(<CoverLetterPage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate cover letter" }));
    expect(await screen.findByRole("article", { name: "Cover letter preview" })).toBeVisible();
    expect(calls.map((c) => c.key)).toContain(GENERATE);
  });

  it("previews the letter with its report, changes and actions", async () => {
    stubApi({ [LATEST]: () => ({ status: 200, body: makeLetter() }) });
    render(<CoverLetterPage jobId={JOB_ID} />);
    const preview = await screen.findByRole("article", { name: "Cover letter preview" });
    for (const text of [
      "Dear Northwind Hiring Team,",
      OPENING,
      DOCKER,
      "Re: ML Engineer, Northwind",
    ])
      expect(within(preview).getByText(text, { exact: false })).toBeVisible();
    // Removed content appears only in the audit, never in the letter.
    expect(within(preview).queryByText(/passionate/)).toBeNull();
    expect(within(preview).queryByText(/60%/)).toBeNull();

    const report = screen.getByRole("region", { name: "Verification report" });
    expect(within(report).getByLabelText("Verification outcome")).toHaveTextContent(/Approved/);
    const changes = screen.getByRole("region", { name: "Changes made during generation" });
    expect(within(changes).getByLabelText("Generation summary")).toHaveTextContent(
      "3 kept1 regenerated1 removed",
    );
    expect(within(changes).getByText("Your evidence says 35%, not 60%.")).toBeVisible();
    expect(within(changes).getByText(/Kubernetes/)).toBeVisible();

    for (const name of ["Edit", "Regenerate"]) expect(screen.getByRole("button", { name }));
    expect(screen.getByRole("link", { name: "Download PDF" })).toHaveAttribute(
      "href",
      "http://localhost:8000/api/v1/cover-letters/l1/download?format=pdf",
    );
    expect(screen.getByRole("link", { name: "Download DOCX" })).toHaveAttribute(
      "href",
      "http://localhost:8000/api/v1/cover-letters/l1/download?format=docx",
    );
  });

  it("shows the evidence behind each sentence on request", async () => {
    stubApi({ [LATEST]: () => ({ status: 200, body: makeLetter() }) });
    render(<CoverLetterPage jobId={JOB_ID} />);
    const preview = await screen.findByRole("article", { name: "Cover letter preview" });
    expect(within(preview).queryByText(/Evidence \(/)).toBeNull();
    fireEvent.click(screen.getByLabelText("Show the evidence behind each sentence"));
    expect(within(preview).getByText(/Evidence \(Machine Learning Intern/)).toBeVisible();
  });

  it("regenerates only after confirmation", async () => {
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    const calls = stubApi({
      [LATEST]: () => ({ status: 200, body: makeLetter() }),
      [GENERATE]: () => ({ status: 201, body: makeLetter({ version: 2 }) }),
    });
    render(<CoverLetterPage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Regenerate" }));
    expect(calls.map((c) => c.key)).not.toContain(GENERATE);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(await screen.findByText(/version 2/)).toBeVisible();
  });

  it("saves edits as text", async () => {
    const calls = stubApi({
      [LATEST]: () => ({ status: 200, body: makeLetter() }),
      [SAVE]: () => ({ status: 200, body: makeLetter({ updated_at: "2026-09-30T11:00:00Z" }) }),
    });
    render(<CoverLetterPage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Closing"), { target: { value: "Kind regards," } });
    fireEvent.click(screen.getByRole("button", { name: "Remove paragraph 3" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("article", { name: "Cover letter preview" });
    expect(calls.find((c) => c.key === SAVE)?.body).toEqual({
      greeting: "Dear Northwind Hiring Team,",
      paragraphs: [OPENING, DOCKER],
      closing: "Kind regards,",
    });
  });

  it("shows why an edit was rejected, next to the paragraph", async () => {
    stubApi({
      [LATEST]: () => ({ status: 200, body: makeLetter() }),
      [SAVE]: () => ({
        status: 422,
        body: {
          detail: [
            {
              type: "value_error",
              loc: ["body", "paragraphs[1]"],
              msg: "Contradicted: “I reduced latency by 80%”. Your evidence says 35%, not 80%.",
            },
          ],
        },
      }),
    });
    render(<CoverLetterPage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const paragraph = screen.getByLabelText("Paragraph 2");
    fireEvent.change(paragraph, { target: { value: "I reduced latency by 80% using ONNX." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(paragraph).toHaveAttribute("aria-invalid", "true"));
    expect(screen.getByText(/35%, not 80%/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("re-verifies and flags a letter that no longer passes", async () => {
    stubApi({
      [LATEST]: () => ({ status: 200, body: makeLetter() }),
      "POST /cover-letters/l1/verify": () => ({
        status: 200,
        body: makeLetter({
          status: "verification_failed",
          report: { ...makeLetter().report!, outcome: "rejected", trigger: "manual" },
        }),
      }),
    });
    render(<CoverLetterPage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Re-verify" }));
    expect(await screen.findByText(/This letter failed verification/)).toBeVisible();
  });
});
