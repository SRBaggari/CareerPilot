import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ClaimResult, VerificationReport } from "@/lib/api/verification";

import { stubApi } from "../profile/testApi";
import { ClaimCheckerPage } from "./ClaimCheckerPage";
import { VerificationReportView } from "./VerificationReportView";

afterEach(() => vi.unstubAllGlobals());

const base: ClaimResult = {
  claim_text: "Built a RAG-based research assistant.",
  claim_type: "statement",
  section: "text",
  position: 0,
  record_id: null,
  cited_evidence_ids: [],
  evidence_ids: ["e1"],
  evidence_source: "retrieved",
  verification_status: "supported",
  confidence: 0.9,
  reason: "Supported by your evidence, though not the evidence it cited.",
  method: "rule_based",
};

const REPORT: VerificationReport = {
  id: null,
  document_type: "text",
  document_id: null,
  trigger: null,
  created_at: null,
  verifier: "rules",
  outcome: "rejected",
  counts: { supported: 1, partially_supported: 1, unsupported: 1, contradicted: 1 },
  claims: [
    base,
    {
      ...base,
      position: 1,
      claim_text: "Built a production RAG platform serving 10,000 users.",
      evidence_ids: [],
      verification_status: "unsupported",
      confidence: 0,
      reason: "Contains numbers or metrics not in the evidence: 10000.",
    },
    {
      ...base,
      position: 2,
      claim_text: "Deployed ML models with Docker and Kubernetes on AWS.",
      verification_status: "partially_supported",
      confidence: 0.4,
      reason: "Names technologies not in the evidence: Kubernetes.",
    },
    {
      ...base,
      position: 3,
      claim_text: "Reduced model inference latency by 60% using ONNX.",
      verification_status: "contradicted",
      confidence: 0,
      method: "llm",
      reason: "Your evidence says 35%, not 60%.",
    },
  ],
  warnings: [],
};

const EVIDENCE = {
  e1: {
    content: "Implemented RAG pipeline for document retrieval and question answering.",
    record_label: "Multi-Agent Research Assistant",
  },
};

/** The top-level claim rows (each may contain its own list of evidence). */
const claimItems = () =>
  Array.from(screen.getByRole("list", { name: "Verified claims" }).querySelectorAll(":scope > li"));

describe("VerificationReportView", () => {
  it("shows the outcome, a count per status, and every claim with its reason", () => {
    render(<VerificationReportView report={REPORT} evidence={EVIDENCE} />);
    expect(screen.getByLabelText("Verification outcome")).toHaveTextContent(/Rejected/);
    const filters = screen.getByRole("group", { name: "Filter by status" });
    for (const label of [
      "All 4",
      "Contradicted 1",
      "Unsupported 1",
      "Partially supported 1",
      "Supported 1",
    ])
      expect(within(filters).getByRole("button", { name: label })).toBeVisible();

    const items = claimItems();
    // Problems first: contradicted, unsupported, partially supported, supported.
    expect(items[0]).toHaveTextContent("Your evidence says 35%, not 60%.");
    expect(items[0]).toHaveTextContent("AI reviewer");
    expect(items[1]).toHaveTextContent("10000");
    expect(items[2]).toHaveTextContent("Kubernetes");
    expect(items[3]).toHaveTextContent("Supported");
    expect(items[3]).toHaveTextContent("confidence 90%");
    expect(items[3]).toHaveTextContent(
      "Evidence (Multi-Agent Research Assistant): “Implemented RAG pipeline",
    );
  });

  it("filters by status", () => {
    render(<VerificationReportView report={REPORT} evidence={EVIDENCE} />);
    fireEvent.click(screen.getByRole("button", { name: "Contradicted 1" }));
    const items = claimItems();
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent("60%");
  });

  it("says when every claim is approved", () => {
    render(
      <VerificationReportView
        report={{
          ...REPORT,
          outcome: "approved",
          claims: [base],
          counts: { supported: 1, partially_supported: 0, unsupported: 0, contradicted: 0 },
        }}
      />,
    );
    expect(screen.getByLabelText("Verification outcome")).toHaveTextContent(/Approved/);
  });
});

describe("ClaimCheckerPage", () => {
  it("checks pasted text and shows the report", async () => {
    const calls = stubApi({ "POST /verification/check": () => ({ status: 200, body: REPORT }) });
    render(<ClaimCheckerPage />);
    const button = screen.getByRole("button", { name: "Check claims" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Text"), {
      target: { value: "Built a RAG-based research assistant." },
    });
    fireEvent.click(button);
    expect(await screen.findByLabelText("Verification outcome")).toHaveTextContent(/Rejected/);
    expect(calls[0].body).toEqual({ text: "Built a RAG-based research assistant." });
  });

  it("shows API errors", async () => {
    stubApi({
      "POST /verification/check": () => ({
        status: 404,
        body: { detail: "Create your profile first." },
      }),
    });
    render(<ClaimCheckerPage />);
    fireEvent.change(screen.getByLabelText("Text"), { target: { value: "Built things." } });
    fireEvent.click(screen.getByRole("button", { name: "Check claims" }));
    expect(await screen.findByText("Create your profile first.")).toBeVisible();
  });
});
