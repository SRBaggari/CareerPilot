import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Job, Requirement } from "@/lib/api/jobs";
import { formatLongDate, formatSalary, groupRequirements } from "@/lib/jobs/labels";

import { stubApi } from "../profile/testApi";
import { JobAnalysisView } from "./JobAnalysisView";
import { JobAnalysisPage, JobsList, JobsPage } from "./JobsPage";
import { NewJobForm } from "./NewJobForm";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
afterEach(() => vi.unstubAllGlobals());

let n = 0;
const req = (
  requirement_type: Requirement["requirement_type"],
  importance: Requirement["importance"],
  description: string,
  extra: Partial<Requirement> = {},
): Requirement => ({
  id: `r${++n}`,
  requirement_type,
  importance,
  description,
  source_excerpt: description,
  min_years: null,
  skill_id: null,
  ...extra,
});

const JOB: Job = {
  id: "j1",
  title: "Senior Machine Learning Engineer",
  company_name: "Northwind Robotics",
  location: "Austin, TX",
  workplace_type: "remote",
  employment_type: "full_time",
  application_deadline: "2026-07-31",
  input_method: "pasted_text",
  requirement_counts: { required: 4, preferred: 2, informational: 2 },
  created_at: "2026-01-01T00:00:00Z",
  source_url: "https://careers.example.test/42",
  description: "Full posting text",
  salary: {
    text: "Salary: $160,000 - $200,000 per year",
    minimum: "160000.00",
    maximum: "200000.00",
    currency: null,
    period: "year",
  },
  analyzer_name: "heuristic",
  analysis_warnings: ["Several work modes are mentioned, so none was set."],
  analyzed_at: "2026-01-01T00:00:00Z",
  requirements: [
    req("technology", "required", "Python", {
      source_excerpt: "Strong proficiency in Python and SQL.",
    }),
    req("skill", "required", "Strong proficiency in Python and SQL."),
    req("technology", "preferred", "Terraform", { source_excerpt: "Familiarity with Terraform." }),
    req("experience", "required", "5+ years of professional experience.", { min_years: "5.0" }),
    req("education", "required", "Bachelor's degree in Computer Science or a related field."),
    req("responsibility", "informational", "Mentor junior engineers."),
    req("eligibility", "required", "Must be authorized to work in the United States."),
    req("technology", "informational", "Kafka"),
  ],
};

describe("JobAnalysisView", () => {
  it("shows every section with importance labels", async () => {
    stubApi({ "GET /jobs/j1": () => ({ status: 200, body: JOB }) });
    render(<JobAnalysisView jobId="j1" />);

    expect(await screen.findByRole("heading", { level: 1, name: JOB.title })).toBeInTheDocument();
    for (const title of [
      "Job overview",
      "Required skills",
      "Preferred skills",
      "Education",
      "Experience",
      "Responsibilities",
      "Eligibility",
    ]) {
      expect(screen.getByRole("region", { name: title })).toBeInTheDocument();
    }
    const overview = screen.getByRole("region", { name: "Job overview" });
    expect(within(overview).getByText("160,000 – 200,000 per year")).toBeInTheDocument();
    expect(within(overview).getByText("Remote")).toBeInTheDocument();
    expect(within(overview).getByText("31 July 2026")).toBeInTheDocument();
    expect(within(overview).getByText("Rule-based")).toBeInTheDocument();

    const required = screen.getByRole("region", { name: "Required skills" });
    expect(within(required).getByText("Python")).toBeInTheDocument();
    expect(within(required).queryByText("Terraform")).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Preferred skills" })).getByText("Terraform"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Experience" })).getByText("(5+ years)"),
    ).toBeInTheDocument();
    const duties = screen.getByRole("region", { name: "Responsibilities" });
    expect(within(duties).getByText("Informational")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Also mentioned" })).getByText("Kafka"),
    ).toBeInTheDocument();
    expect(screen.getByText(/several work modes are mentioned/i)).toBeInTheDocument();
  });

  it("says when a section is not stated instead of guessing", async () => {
    stubApi({
      "GET /jobs/j1": () => ({ status: 200, body: { ...JOB, salary: null, requirements: [] } }),
    });
    render(<JobAnalysisView jobId="j1" />);
    expect(await screen.findByText("No education requirements are stated.")).toBeInTheDocument();
    expect(screen.getByText("No eligibility conditions are stated.")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Job overview" })).getAllByText("Not stated")
        .length,
    ).toBeGreaterThan(0);
  });
});

describe("NewJobForm", () => {
  it("analyzes a pasted description", async () => {
    const calls = stubApi({ "POST /jobs/analyze": () => ({ status: 201, body: JOB }) });
    const onCreated = vi.fn();
    render(<NewJobForm onCreated={onCreated} />);
    const button = screen.getByRole("button", { name: "Analyze job" });
    expect(button).toBeDisabled(); // too short
    fireEvent.change(screen.getByLabelText(/job description/i), {
      target: { value: "x".repeat(80) },
    });
    fireEvent.change(screen.getByLabelText(/job posting url/i), {
      target: { value: "careers.example.test/42" },
    });
    fireEvent.click(button);
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(JOB));
    expect(calls.find((c) => c.key === "POST /jobs/analyze")?.body).toEqual({
      description: "x".repeat(80),
      source_url: "careers.example.test/42",
      title: null,
      company_name: null,
      location: null,
    });
  });

  it("asks for the title and company when they aren't in the description", async () => {
    let attempt = 0;
    const calls = stubApi({
      "POST /jobs/analyze": () =>
        ++attempt === 1
          ? {
              status: 422,
              body: {
                detail: [
                  {
                    loc: ["body", "title"],
                    msg: "The job title wasn't found in the description. Please enter it.",
                  },
                  {
                    loc: ["body", "company_name"],
                    msg: "The company wasn't found in the description. Please enter it.",
                  },
                ],
              },
            }
          : { status: 201, body: JOB },
    });
    const onCreated = vi.fn();
    render(<NewJobForm onCreated={onCreated} />);
    fireEvent.change(screen.getByLabelText(/job description/i), {
      target: { value: "y".repeat(80) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Analyze job" }));

    expect(await screen.findByText(/job title wasn't found/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^job title/i), { target: { value: "Data Analyst" } });
    fireEvent.change(screen.getByLabelText(/^company/i), { target: { value: "Contoso" } });
    fireEvent.click(screen.getByRole("button", { name: "Analyze job" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(calls.at(-1)?.body).toMatchObject({ title: "Data Analyst", company_name: "Contoso" });
  });

  it("saves a manually entered job with classified requirements", async () => {
    const calls = stubApi({ "POST /jobs": () => ({ status: 201, body: JOB }) });
    const onCreated = vi.fn();
    render(<NewJobForm onCreated={onCreated} />);
    fireEvent.click(screen.getByRole("tab", { name: "Enter manually" }));
    fireEvent.change(screen.getByLabelText(/^job title/i), {
      target: { value: "Frontend Engineer" },
    });
    fireEvent.change(screen.getByLabelText(/^company/i), { target: { value: "Fabrikam" } });
    fireEvent.change(screen.getByLabelText("Requirement 1"), { target: { value: "React" } });
    fireEvent.change(screen.getByLabelText("Requirement 1 type"), {
      target: { value: "technology" },
    });
    fireEvent.click(screen.getByRole("button", { name: "+ Add requirement" }));
    fireEvent.change(screen.getByLabelText("Requirement 2"), {
      target: { value: "Design systems" },
    });
    fireEvent.change(screen.getByLabelText("Requirement 2 importance"), {
      target: { value: "preferred" },
    });
    fireEvent.click(screen.getByRole("button", { name: "+ Add requirement" })); // left blank: ignored
    fireEvent.click(screen.getByRole("button", { name: "Save job" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    const body = calls.find((c) => c.key === "POST /jobs")?.body as { requirements: unknown[] };
    expect(body).toMatchObject({
      title: "Frontend Engineer",
      company_name: "Fabrikam",
      location: null,
    });
    expect(body.requirements).toEqual([
      { requirement_type: "technology", importance: "required", description: "React" },
      { requirement_type: "skill", importance: "preferred", description: "Design systems" },
    ]);
  });
});

describe("helpers", () => {
  it("groups skills by importance and keeps duties separate", () => {
    const groups = groupRequirements(JOB.requirements);
    expect(groups.requiredSkills.map((r) => r.description)).toEqual([
      "Python",
      "Strong proficiency in Python and SQL.",
    ]);
    expect(groups.preferredSkills.map((r) => r.description)).toEqual(["Terraform"]);
    expect(groups.mentionedTechnologies.map((r) => r.description)).toEqual(["Kafka"]);
    expect(groups.responsibilities).toHaveLength(1);
  });

  it("formats salary without inventing a currency", () => {
    expect(
      formatSalary({
        text: "x",
        minimum: "40000",
        maximum: null,
        currency: "INR",
        period: "month",
      }),
    ).toBe("INR 40,000 per month");
    expect(
      formatSalary({ text: "$90k", minimum: null, maximum: null, currency: null, period: null }),
    ).toBeNull();
    expect(formatLongDate("2026-08-15")).toBe("15 August 2026");
  });

  it("lists jobs with their requirement counts", () => {
    render(<JobsList jobs={[JOB]} />);
    expect(screen.getByRole("link", { name: /senior machine learning engineer/i })).toHaveAttribute(
      "href",
      "/jobs/j1",
    );
    expect(screen.getByText("4 required")).toBeInTheDocument();
  });
});

describe("JobsPage and JobAnalysisPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists jobs and links to analyzing a new one", async () => {
    stubApi({
      "GET /jobs": () => ({
        status: 200,
        body: [
          {
            id: "j1",
            title: "ML Engineer",
            company_name: "Northwind",
            location: "Remote",
            workplace_type: "remote",
            employment_type: null,
            application_deadline: null,
            input_method: "pasted_text",
            requirement_counts: { required: 3, preferred: 1, informational: 0 },
            created_at: "2026-09-30T10:00:00Z",
          },
        ],
      }),
    });
    render(<JobsPage />);
    expect(await screen.findByRole("link", { name: /ML Engineer/ })).toHaveAttribute(
      "href",
      "/jobs/j1",
    );
    expect(screen.getByRole("link", { name: "Analyze a job" })).toHaveAttribute("href", "/analyze");
  });

  it("analyzes a job from its own page", () => {
    render(<JobAnalysisPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Job Analysis" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Analyze a job" })).toBeVisible();
  });
});
