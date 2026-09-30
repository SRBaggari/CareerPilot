import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Recommendation, RecommendationList } from "@/lib/api/recommendations";

import { stubApi } from "../profile/testApi";
import { RecommendationsPage } from "./RecommendationsPage";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

beforeEach(() => push.mockReset());
afterEach(() => vi.unstubAllGlobals());

function makeRec(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    id: "r1",
    source: "mock",
    source_identifier: "mock-1006",
    title: "ML Engineer",
    company: "Northwind Robotics",
    location: "Remote, India",
    url: "https://jobs.example.com/mock/1006",
    work_mode: "remote",
    employment_type: "full_time",
    experience_level: "entry_level",
    posted_date: "2026-09-27",
    deadline: "2026-11-01",
    status: "new",
    eligible: true,
    exclusions: [],
    concerns: ["Must be authorized to work in India. CareerPilot can't verify this."],
    explanation: {
      summary:
        "Recommended because your evidence covers 3 of 3 required requirements, including Python, SQL and Docker; remote, as you prefer.",
      reasons: [
        "Your verified evidence covers 3 of 3 required requirements: Python, SQL and Docker.",
        "Your Multi-Agent Research Assistant project is relevant: it covers RAG.",
        "Remote, as you prefer.",
      ],
      required_met: 3,
      required_partial: 0,
      required_total: 3,
      matched_skills: [
        { skill: "Python", importance: "required", explanation: "Your evidence shows Python." },
        { skill: "RAG", importance: "preferred", explanation: "Your evidence shows RAG." },
      ],
      partial_skills: [],
      missing_skills: [
        {
          skill: "Kubernetes",
          importance: "preferred",
          explanation: "No verified evidence mentions Kubernetes.",
        },
      ],
      requirements: [
        {
          requirement: "Python",
          requirement_type: "technology",
          importance: "required",
          status: "matched",
          explanation: "Your evidence shows Python.",
        },
        {
          requirement: "Kubernetes",
          requirement_type: "technology",
          importance: "preferred",
          status: "missing",
          explanation: "No verified evidence mentions Kubernetes.",
        },
      ],
      relevant_projects: [
        {
          project_id: "p1",
          title: "Multi-Agent Research Assistant",
          evidence: ["Implemented RAG pipeline for document retrieval and question answering."],
          supports: ["RAG"],
        },
      ],
      preference_fit: ["Remote, as you prefer."],
      disclaimer:
        "Recommendations explain how your verified evidence covers each job's stated requirements.",
    },
    required_coverage: 1,
    overall_coverage: 0.9,
    rank: 1,
    computed_at: "2026-09-30T10:00:00Z",
    is_stale: false,
    job_id: null,
    application: null,
    ...overrides,
  };
}

function makeList(overrides: Partial<RecommendationList> = {}): RecommendationList {
  return {
    recommendations: [makeRec()],
    counts: { recommended: 1, saved: 0, ignored: 0, filtered_out: 2 },
    refreshed_at: "2026-09-30T10:00:00Z",
    errors: [],
    sources: [],
    ...overrides,
  };
}

const result = (rec: Partial<Recommendation>, extra = {}) => ({
  status: 200,
  body: {
    recommendation: makeRec(rec),
    job_id: rec.job_id ?? null,
    application_id: null,
    ...extra,
  },
});

describe("RecommendationsPage", () => {
  it("shows each job with why it is recommended", async () => {
    stubApi({ "GET /recommendations?view=recommended": () => ({ status: 200, body: makeList() }) });
    render(<RecommendationsPage />);
    const card = await screen.findByRole("article", { name: "#1ML Engineer" });
    expect(card).toHaveTextContent("Northwind Robotics · Remote, India");
    expect(within(card).getByLabelText("Required requirements covered")).toHaveTextContent(
      "3 of 3",
    );
    expect(
      within(card).getByText(/^Recommended because your evidence covers 3 of 3/),
    ).toBeVisible();
    expect(within(card).getByLabelText("Reasons")).toHaveTextContent("Python, SQL and Docker");
    expect(within(card).getByLabelText("Matched skills")).toHaveTextContent(
      "PythonRAG (preferred)",
    );
    expect(within(card).getByLabelText("Missing skills")).toHaveTextContent("Kubernetes");
    expect(within(card).getByLabelText("Eligibility concerns")).toHaveTextContent(
      /authorized to work/,
    );
    const projects = within(card).getByLabelText("Relevant projects");
    expect(projects).toHaveTextContent("Multi-Agent Research Assistant covers RAG");
    expect(projects).toHaveTextContent("“Implemented RAG pipeline");
    for (const name of [
      "Save Job",
      "Ignore Job",
      "Analyze Job",
      "Tailor Resume",
      "Start Application",
    ])
      expect(within(card).getByRole("button", { name })).toBeEnabled();

    fireEvent.click(within(card).getByRole("button", { name: "Show every requirement" }));
    expect(within(card).getByLabelText("Requirement details")).toHaveTextContent(
      "missing (preferred) Kubernetes",
    );
  });

  it("refreshes and shows source problems", async () => {
    const calls = stubApi({
      "GET /recommendations?view=recommended": () => ({
        status: 200,
        body: makeList({
          recommendations: [],
          refreshed_at: null,
          counts: { recommended: 0, saved: 0, ignored: 0, filtered_out: 0 },
        }),
      }),
      "POST /recommendations/refresh": () => ({
        status: 200,
        body: makeList({ errors: ["Broken API is unavailable: timed out"] }),
      }),
    });
    render(<RecommendationsPage />);
    expect(await screen.findByText(/No recommendations yet/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh recommendations" }));
    expect(await screen.findByRole("article", { name: "#1ML Engineer" })).toBeVisible();
    expect(screen.getByText("Broken API is unavailable: timed out")).toBeVisible();
    expect(calls.map((c) => c.key)).toContain("POST /recommendations/refresh");
  });

  it("switches views, including filtered-out jobs with their reasons", async () => {
    stubApi({
      "GET /recommendations?view=recommended": () => ({ status: 200, body: makeList() }),
      "GET /recommendations?view=filtered_out": () => ({
        status: 200,
        body: makeList({
          recommendations: [
            makeRec({
              id: "r2",
              title: "Senior Machine Learning Engineer",
              eligible: false,
              rank: null,
              exclusions: [
                "Requires 5+ years of experience; your work history adds up to about 1.0.",
              ],
            }),
          ],
        }),
      }),
    });
    render(<RecommendationsPage />);
    await screen.findByRole("article", { name: "#1ML Engineer" });
    fireEvent.click(screen.getByRole("button", { name: "Filtered out 2" }));
    const card = await screen.findByRole("article", { name: "Senior Machine Learning Engineer" });
    expect(within(card).getByLabelText("Exclusions")).toHaveTextContent(/Requires 5\+ years/);
  });

  it("saves and ignores jobs", async () => {
    stubApi({
      "GET /recommendations?view=recommended": () => ({ status: 200, body: makeList() }),
      "POST /recommendations/r1/save": () => result({ status: "saved" }),
      "POST /recommendations/r1/ignore": () => result({ status: "ignored" }),
    });
    render(<RecommendationsPage />);
    const card = await screen.findByRole("article", { name: "#1ML Engineer" });
    fireEvent.click(within(card).getByRole("button", { name: "Save Job" }));
    expect(await within(card).findByRole("button", { name: "Unsave" })).toBeVisible();
    expect(card).toHaveTextContent("Saved");
    fireEvent.click(within(card).getByRole("button", { name: "Ignore Job" }));
    expect(await within(card).findByRole("button", { name: "Restore" })).toBeVisible();
  });

  it("analyzes a job and opens its match; tailor opens the resume", async () => {
    stubApi({
      "GET /recommendations?view=recommended": () => ({ status: 200, body: makeList() }),
      "POST /recommendations/r1/analyze": () => result({ job_id: "job-9" }),
    });
    render(<RecommendationsPage />);
    const card = await screen.findByRole("article", { name: "#1ML Engineer" });
    fireEvent.click(within(card).getByRole("button", { name: "Analyze Job" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/jobs/job-9/match"));
    fireEvent.click(within(card).getByRole("button", { name: "Tailor Resume" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/jobs/job-9/resume"));
  });

  it("starts a draft application without submitting anything", async () => {
    stubApi({
      "GET /recommendations?view=recommended": () => ({ status: 200, body: makeList() }),
      "POST /recommendations/r1/start-application": () =>
        result(
          { job_id: "job-9", status: "saved", application: { id: "a1", status: "draft" } },
          { application_id: "a1" },
        ),
    });
    render(<RecommendationsPage />);
    const card = await screen.findByRole("article", { name: "#1ML Engineer" });
    fireEvent.click(within(card).getByRole("button", { name: "Start Application" }));
    expect(
      await within(card).findByRole("link", { name: "Continue application (draft)" }),
    ).toHaveAttribute("href", "/jobs/job-9/questions");
    expect(
      within(card).getByText(/Nothing is submitted until you review and approve it/),
    ).toBeVisible();
    expect(card).toHaveTextContent("Application: draft");
  });
});
