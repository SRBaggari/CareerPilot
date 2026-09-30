import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MatchReport, RequirementMatch } from "@/lib/api/matching";

import { stubApi } from "../profile/testApi";
import { JobMatchPage } from "./JobMatchPage";
import { MatchReportView, percent } from "./MatchReportView";

afterEach(() => vi.unstubAllGlobals());

const rag: RequirementMatch = {
  requirement_id: "r1",
  requirement: "Experience with Retrieval-Augmented Generation",
  requirement_type: "skill",
  importance: "required",
  matching_candidate_evidence: [
    {
      evidence_id: "e1",
      factual_content: "Implemented RAG pipeline in Multi-Agent Research Assistant.",
      similarity: 0.52,
      source: {
        record_type: "project",
        record_id: "p1",
        record_label: "Multi-Agent Research Assistant",
        origin: "resume_extracted",
        resume_id: "res1",
        resume_file_name: "priya.pdf",
      },
    },
  ],
  semantic_similarity: 0.52,
  match_status: "matched",
  explanation: "Your evidence covers RAG.",
  evidence_ids: ["e1"],
  judge: "rules",
  details: {},
};
const item = (overrides: Partial<RequirementMatch>): RequirementMatch => ({
  ...rag,
  matching_candidate_evidence: [],
  evidence_ids: [],
  ...overrides,
});
const kubernetes = item({
  requirement_id: "r2",
  requirement: "Kubernetes",
  requirement_type: "technology",
  match_status: "missing",
  explanation: "No verified evidence mentions Kubernetes.",
});
const terraform = item({
  requirement_id: "r3",
  requirement: "Terraform",
  requirement_type: "technology",
  importance: "preferred",
  match_status: "partial",
  explanation: "Terraform is in your skills list.",
});
const authorization = item({
  requirement_id: "r4",
  requirement: "Must be authorized to work in the US",
  requirement_type: "eligibility",
  match_status: "unknown",
  explanation: "CareerPilot can't verify this from your profile.",
});

const REPORT: MatchReport = {
  job_id: "j1",
  job_title: "ML Engineer",
  company_name: "Northwind",
  candidate_id: "c1",
  computed_at: "2026-09-30T10:00:00Z",
  matcher: "rules",
  embedding_model: "hash-v1",
  scoring_version: "coverage-v1",
  disclaimer:
    "This score shows how much of the job's stated requirements your verified evidence covers. It is not a prediction or guarantee of being hired.",
  summary: "1 matched, 1 partial, 1 missing, 1 could not be assessed, out of 4 requirements.",
  scores: {
    evidence_coverage: 0.4167,
    required_coverage: 0.5,
    preferred_coverage: 0.5,
    semantic_similarity: 0.2,
    skill_coverage: 0.5,
  },
  status_counts: { matched: 1, partial: 1, missing: 1, unknown: 1 },
  requirements: [rag, kubernetes, terraform, authorization],
  strongest_matches: [rag],
  missing_skills: [kubernetes],
  partial_matches: [terraform],
  potentially_disqualifying: [authorization],
  informational_not_scored: 3,
  warnings: [],
  is_stale: false,
};

describe("MatchReportView", () => {
  it("shows coverage with the disclaimer and every highlight section", () => {
    render(<MatchReportView report={REPORT} busy={false} onRecompute={vi.fn()} />);
    const scores = screen.getByRole("region", { name: "Evidence coverage" });
    expect(within(scores).getByText(/not a prediction or guarantee/i)).toBeInTheDocument();
    expect(within(scores).getByText("42%")).toBeInTheDocument();
    expect(within(scores).getByText(/3 informational statement/)).toBeInTheDocument();

    const disqualifying = screen.getByRole("region", { name: "Potentially disqualifying" });
    expect(
      within(disqualifying).getByText("Must be authorized to work in the US"),
    ).toBeInTheDocument();

    const strongest = screen.getByRole("region", { name: "Strongest matches" });
    expect(
      within(strongest).getByText("Implemented RAG pipeline in Multi-Agent Research Assistant."),
    ).toBeInTheDocument();
    expect(
      within(strongest).getByText(
        /Multi-Agent Research Assistant · from priya\.pdf · similarity 0\.52/,
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Missing skills" })).getByText("Kubernetes"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Partial matches" })).getByText("Terraform"),
    ).toBeInTheDocument();
  });

  it("filters the full list by status", () => {
    render(<MatchReportView report={REPORT} busy={false} onRecompute={vi.fn()} />);
    const all = screen.getByRole("region", { name: "All requirements" });
    expect(
      within(all)
        .getAllByRole("listitem")
        .filter((li) => li.closest("ul[aria-label]") === null),
    ).toHaveLength(4);
    fireEvent.click(within(all).getByRole("button", { name: "Missing (1)" }));
    expect(within(all).getByText("Kubernetes")).toBeInTheDocument();
    expect(within(all).queryByText("Terraform")).not.toBeInTheDocument();
  });

  it("warns when the match is stale and offers to update it", () => {
    const onRecompute = vi.fn();
    render(
      <MatchReportView
        report={{ ...REPORT, is_stale: true }}
        busy={false}
        onRecompute={onRecompute}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Update match" }));
    expect(onRecompute).toHaveBeenCalled();
  });

  it("formats percentages", () => {
    expect(percent(0.755)).toBe("76%");
    expect(percent(null)).toBe("—");
  });
});

describe("JobMatchPage", () => {
  it("runs the match when none exists yet", async () => {
    const calls = stubApi({
      "GET /jobs/j1/match": () => ({
        status: 404,
        body: { detail: "This job hasn't been matched against your profile yet." },
      }),
      "POST /jobs/j1/match": () => ({ status: 200, body: REPORT }),
    });
    render(<JobMatchPage jobId="j1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Run match" }));
    expect(await screen.findByRole("region", { name: "Strongest matches" })).toBeInTheDocument();
    expect(screen.getByText("ML Engineer · Northwind")).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toContain("POST /jobs/j1/match");
  });

  it("points to the profile when there isn't one", async () => {
    stubApi({
      "GET /jobs/j1/match": () => ({
        status: 404,
        body: { detail: "Profile not found. Create your profile first." },
      }),
      "POST /jobs/j1/match": () => ({
        status: 404,
        body: { detail: "Profile not found. Create your profile first." },
      }),
    });
    render(<JobMatchPage jobId="j1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Run match" }));
    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Go to your profile" })).toHaveAttribute(
        "href",
        "/profile",
      ),
    );
  });
});
