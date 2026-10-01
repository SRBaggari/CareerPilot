import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { stubApi } from "../profile/testApi";
import { CoverLettersPage, ResumeBuilderPage } from "./DocumentsHub";

afterEach(() => vi.unstubAllGlobals());

const JOBS = [
  { id: "j1", title: "ML Engineer", company_name: "Northwind", created_at: "2026-09-29T08:00:00Z" },
  { id: "j2", title: "Data Analyst", company_name: "Bluefin", created_at: "2026-09-28T08:00:00Z" },
  {
    id: "j3",
    title: "Backend Engineer",
    company_name: "Ledgerly",
    created_at: "2026-09-27T08:00:00Z",
  },
];
const doc = (status: string) => ({
  id: `d-${status}`,
  version: 2,
  status,
  updated_at: "2026-09-30T10:00:00Z",
});

describe("ResumeBuilderPage", () => {
  it("shows the uploaded resume and each job's tailored resume", async () => {
    stubApi({
      "GET /resumes": () => ({
        status: 200,
        body: [
          {
            id: "u1",
            file_name: "priya.docx",
            parse_status: "parsed",
            is_primary: true,
            created_at: "2026-09-01T10:00:00Z",
          },
        ],
      }),
      "GET /jobs": () => ({ status: 200, body: JOBS }),
      "GET /jobs/j1/tailored-resumes/latest": () => ({ status: 200, body: doc("verified") }),
      "GET /jobs/j2/tailored-resumes/latest": () => ({ status: 404, body: { detail: "None" } }),
      "GET /jobs/j3/tailored-resumes/latest": () => ({ status: 500, body: { detail: "boom" } }),
    });
    render(<ResumeBuilderPage />);
    expect(await screen.findByLabelText("Uploaded resumes")).toHaveTextContent("priya.docx");
    const list = await screen.findByRole("list", { name: "resumes by job" });
    const [ml, data, backend] = within(list).getAllByRole("listitem");
    expect(ml).toHaveTextContent("Verified");
    expect(ml).toHaveTextContent("version 2");
    expect(within(ml).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/jobs/j1/resume",
    );
    expect(within(ml).getByRole("link", { name: "PDF" }).getAttribute("href")).toContain(
      "/tailored-resumes/d-verified/download?format=pdf",
    );
    expect(within(data).getByRole("link", { name: "Create" })).toHaveAttribute(
      "href",
      "/jobs/j2/resume",
    );
    expect(backend).toHaveTextContent("Couldn't load"); // one failure doesn't hide the rest
  });

  it("points to the profile when no resume is uploaded", async () => {
    stubApi({
      "GET /resumes": () => ({ status: 200, body: [] }),
      "GET /jobs": () => ({ status: 200, body: [] }),
    });
    render(<ResumeBuilderPage />);
    expect(await screen.findByRole("link", { name: "Upload it in your profile" })).toHaveAttribute(
      "href",
      "/profile",
    );
    expect(await screen.findByRole("link", { name: "Analyze a job" })).toHaveAttribute(
      "href",
      "/analyze",
    );
  });
});

describe("CoverLettersPage", () => {
  it("lists each job's cover letter and its status", async () => {
    stubApi({
      "GET /jobs": () => ({ status: 200, body: JOBS.slice(0, 2) }),
      "GET /jobs/j1/cover-letters/latest": () => ({
        status: 200,
        body: doc("verification_failed"),
      }),
      "GET /jobs/j2/cover-letters/latest": () => ({ status: 404, body: { detail: "None" } }),
    });
    render(<CoverLettersPage />);
    const list = await screen.findByRole("list", { name: "cover letters by job" });
    const [ml, data] = within(list).getAllByRole("listitem");
    expect(ml).toHaveTextContent("Needs fixing");
    expect(within(ml).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/jobs/j1/cover-letter",
    );
    expect(within(data).getByRole("link", { name: "Create" })).toBeVisible();
  });
});
