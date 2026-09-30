import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  discoveryQuery,
  EMPTY_FILTERS,
  type DiscoveredJob,
  type DiscoveryResults,
} from "@/lib/api/discovery";

import { DiscoveryPage } from "./DiscoveryPage";

afterEach(() => vi.unstubAllGlobals());

const posting: DiscoveredJob = {
  source: "mock",
  source_identifier: "mock-1006",
  title: "ML Engineer",
  company: "Northwind Robotics",
  location: "Remote, India",
  url: "https://jobs.example.com/mock/1006",
  description: "Northwind Robotics is hiring an ML Engineer.\n\nRequirements:\n- Python and SQL.",
  employment_type: "full_time",
  work_mode: "remote",
  posted_date: "2026-09-27",
  deadline: "2026-11-01",
  skills: ["Python", "SQL", "Docker", "RAG"],
  experience_level: "entry_level",
  matched_skills: ["Python"],
  imported_job_id: null,
};

function results(overrides: Partial<DiscoveryResults> = {}): DiscoveryResults {
  return {
    jobs: [posting],
    total: 1,
    page: 1,
    page_size: 10,
    sources: [
      {
        name: "mock",
        display_name: "Sample jobs (development)",
        access_kind: "mock",
        description: "Built-in sample postings.",
        terms_url: null,
        enabled: true,
        reasons: [],
      },
    ],
    errors: [],
    ...overrides,
  };
}

/** Stub every search (whatever the query string) with one handler, recording the queries. */
function stubSearch(handler: (query: string) => DiscoveryResults, extra = {}) {
  const queries: string[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace("http://localhost:8000/api/v1", "");
    if (path.startsWith("/discovery/jobs?")) {
      const query = path.split("?")[1];
      queries.push(query);
      return new Response(JSON.stringify(handler(query)), { status: 200 });
    }
    const key = `${init?.method ?? "GET"} ${path}`;
    const found = (extra as Record<string, () => { status: number; body: unknown }>)[key];
    if (!found) return new Response(JSON.stringify({ detail: `unmocked ${key}` }), { status: 500 });
    const r = found();
    return new Response(JSON.stringify(r.body), { status: r.status });
  });
  vi.stubGlobal("fetch", fetchMock);
  return queries;
}

describe("discoveryQuery", () => {
  it("encodes every filter, repeating multi-valued ones", () => {
    const query = discoveryQuery(
      {
        role: " ML engineer ",
        location: "Hyderabad",
        remote: "true",
        employmentTypes: ["internship", "full_time"],
        skills: ["Python", "C++"],
        experienceLevels: ["student", "entry_level"],
      },
      2,
      10,
    );
    expect(query).toBe(
      "role=ML+engineer&location=Hyderabad&remote=true&employment_type=internship&employment_type=full_time&skills=Python&skills=C%2B%2B&experience_level=student&experience_level=entry_level&page=2&page_size=10",
    );
    expect(discoveryQuery(EMPTY_FILTERS)).toBe("page=1&page_size=10");
  });
});

describe("DiscoveryPage", () => {
  it("lists postings with their details, skills and the original link", async () => {
    stubSearch(() => results());
    render(<DiscoveryPage />);
    const item = await screen.findByRole("listitem", { name: "ML Engineer" });
    expect(item).toHaveTextContent("Northwind Robotics · Remote, India");
    for (const text of [
      "Remote",
      "Full-time",
      "Entry level",
      "Posted 2026-09-27",
      "Apply by 2026-11-01",
    ])
      expect(item).toHaveTextContent(text);
    expect(within(item).getByLabelText("Skills in this posting")).toHaveTextContent(
      "PythonSQLDockerRAG",
    );
    const link = within(item).getByRole("link", { name: "View original posting" });
    expect(link).toHaveAttribute("href", "https://jobs.example.com/mock/1006");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("1 job found")).toBeVisible();

    fireEvent.click(within(item).getByRole("button", { name: "Show description" }));
    expect(within(item).getByText(/Requirements:/)).toBeVisible();
  });

  it("searches with every filter", async () => {
    const queries = stubSearch(() => results());
    render(<DiscoveryPage />);
    await screen.findByRole("listitem", { name: "ML Engineer" });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "ML engineer" } });
    fireEvent.change(screen.getByLabelText("Location"), { target: { value: "Hyderabad" } });
    fireEvent.change(screen.getByLabelText("Remote"), { target: { value: "true" } });
    fireEvent.change(screen.getByLabelText("Skills"), { target: { value: "Python, Docker" } });
    fireEvent.click(screen.getByLabelText("Internship"));
    fireEvent.click(screen.getByLabelText("Full-time"));
    fireEvent.click(screen.getByLabelText("Entry level"));
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(queries).toHaveLength(2));
    const params = new URLSearchParams(queries[1]);
    expect(params.get("role")).toBe("ML engineer");
    expect(params.get("location")).toBe("Hyderabad");
    expect(params.get("remote")).toBe("true");
    expect(params.getAll("skills")).toEqual(["Python", "Docker"]);
    expect(params.getAll("employment_type")).toEqual(["internship", "full_time"]);
    expect(params.getAll("experience_level")).toEqual(["entry_level"]);
  });

  it("imports a posting and links to the job and its match", async () => {
    stubSearch(() => results(), {
      "POST /discovery/jobs/mock/mock-1006/import": () => ({
        status: 201,
        body: { job_id: "job-9", created: true },
      }),
    });
    render(<DiscoveryPage />);
    const item = await screen.findByRole("listitem", { name: "ML Engineer" });
    fireEvent.click(within(item).getByRole("button", { name: "Import & analyze" }));
    expect(await within(item).findByRole("link", { name: "Open job" })).toHaveAttribute(
      "href",
      "/jobs/job-9",
    );
    expect(within(item).getByRole("link", { name: "Compare with my profile" })).toHaveAttribute(
      "href",
      "/jobs/job-9/match",
    );
    expect(item).toHaveTextContent("Imported");
  });

  it("shows source problems without hiding other results, and pages through results", async () => {
    const queries = stubSearch((query) =>
      results({
        total: 25,
        page: Number(new URLSearchParams(query).get("page")),
        errors: ["Guarded board: The source showed a CAPTCHA or bot challenge."],
      }),
    );
    render(<DiscoveryPage />);
    expect(await screen.findByText(/CAPTCHA or bot challenge/)).toBeVisible();
    expect(screen.getByText("25 jobs found")).toBeVisible();
    expect(screen.getByText("Page 1 of 3")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2 of 3")).toBeVisible();
    expect(new URLSearchParams(queries[1]).get("page")).toBe("2");
  });

  it("says when nothing matches", async () => {
    stubSearch(() => results({ jobs: [], total: 0 }));
    render(<DiscoveryPage />);
    expect(await screen.findByText("No jobs match these filters.")).toBeVisible();
  });
});
