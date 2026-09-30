import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TailoredResume } from "@/lib/api/tailoredResumes";

import { stubApi } from "../profile/testApi";
import { dateRange } from "./ResumePreview";
import { TailoredResumePage } from "./TailoredResumePage";

afterEach(() => vi.unstubAllGlobals());

const JOB_ID = "j1";
const LATEST = `GET /jobs/${JOB_ID}/tailored-resumes/latest`;
const GENERATE = `POST /jobs/${JOB_ID}/tailored-resumes`;
const DOCKER = "Deployed ML models with Docker on AWS.";

function makeResume(overrides: Partial<TailoredResume> = {}): TailoredResume {
  return {
    id: "t1",
    job_id: JOB_ID,
    job_title: "ML Engineer",
    company_name: "Northwind",
    version: 1,
    status: "verified",
    generator: "llm:claude-opus-5-5",
    created_at: "2026-09-30T10:00:00Z",
    updated_at: "2026-09-30T10:00:00Z",
    content: {
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
      summary: [],
      skills: [
        { claim_id: "s1", text: "Python", evidence_ids: ["e2"] },
        { claim_id: "s2", text: "Docker", evidence_ids: ["e1"] },
      ],
      experience: [
        {
          record_id: "w1",
          title: "Machine Learning Intern",
          company_name: "Acme Analytics",
          location: null,
          start_date: "2023-05-01",
          end_date: "2024-05-01",
          is_current: false,
          bullets: [{ claim_id: "c1", text: DOCKER, evidence_ids: ["e1"] }],
        },
      ],
      projects: [
        {
          record_id: "p1",
          title: "Multi-Agent Research Assistant",
          role: null,
          url: null,
          start_date: null,
          end_date: null,
          bullets: [
            { claim_id: "c2", text: "Built data pipelines in Python.", evidence_ids: ["e2"] },
          ],
        },
        {
          record_id: "p2",
          title: "Budget Tracker",
          role: null,
          url: null,
          start_date: null,
          end_date: null,
          bullets: [],
        },
      ],
      education: [],
      certifications: [],
      achievements: [],
      coursework: [{ record_id: "k1", course_name: "Machine Learning" }],
    },
    verification: {
      verified_claims: 4,
      rewritten: 1,
      rejected: 1,
      audit: [
        {
          section: "Experience · Machine Learning Intern",
          original_text: "Deployed 50 ML models with Docker on AWS.",
          final_text: DOCKER,
          outcome: "rewritten",
          verdict: "unsupported",
          reason: "Contains numbers or metrics not in the evidence: 50.",
        },
        {
          section: "skills",
          original_text: "Kubernetes",
          final_text: null,
          outcome: "rejected",
          verdict: "unsupported",
          reason: "Not in the candidate's skill list.",
        },
      ],
    },
    notes: ["Left out of Skills because no verified evidence mentions them: Terraform."],
    evidence: {
      e1: { content: DOCKER, record_label: "Machine Learning Intern at Acme Analytics" },
      e2: { content: "Built data pipelines in Python and SQL.", record_label: "Multi-Agent" },
    },
    ...overrides,
  };
}

describe("dateRange", () => {
  it("formats record dates without changing them", () => {
    expect(dateRange("2023-05-01", "2024-05-01")).toBe("May 2023 – May 2024");
    expect(dateRange("2023-05-01", null, true)).toBe("May 2023 – Present");
    expect(dateRange(null, null)).toBe("");
  });
});

describe("TailoredResumePage", () => {
  it("offers to generate when no resume exists yet", async () => {
    const resume = makeResume();
    const calls = stubApi({
      [LATEST]: () => ({ status: 404, body: { detail: "No tailored resume for this job yet." } }),
      [GENERATE]: () => ({ status: 201, body: resume }),
    });
    render(<TailoredResumePage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Generate tailored resume" }));
    expect(await screen.findByRole("article", { name: "Resume preview" })).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toContain(GENERATE);
  });

  it("previews the resume with its verification report and actions", async () => {
    stubApi({ [LATEST]: () => ({ status: 200, body: makeResume() }) });
    render(<TailoredResumePage jobId={JOB_ID} />);
    const preview = await screen.findByRole("article", { name: "Resume preview" });
    expect(within(preview).getByText("Machine Learning Intern, Acme Analytics")).toBeVisible();
    expect(within(preview).getByText("May 2023 – May 2024")).toBeVisible();
    expect(within(preview).getByText("Python, Docker")).toBeVisible();
    expect(within(preview).getByText("Machine Learning")).toBeVisible();
    // Rejected content never appears in the preview, only in the audit.
    expect(within(preview).queryByText(/Kubernetes/)).toBeNull();
    expect(within(preview).queryByText(/50 ML models/)).toBeNull();

    const panel = screen.getByRole("region", { name: "Claim verification" });
    expect(within(panel).getByLabelText("Verification summary")).toHaveTextContent(
      "4 verified1 rewritten1 rejected",
    );
    expect(within(panel).getByText("Deployed 50 ML models with Docker on AWS.")).toBeVisible();
    expect(within(panel).getByText(/metrics not in the evidence: 50/)).toBeVisible();
    expect(within(panel).getByText(/Terraform/)).toBeVisible();

    for (const name of ["Edit", "Regenerate"]) expect(screen.getByRole("button", { name }));
    expect(screen.getByRole("link", { name: "Download PDF" })).toHaveAttribute(
      "href",
      "http://localhost:8000/api/v1/tailored-resumes/t1/download?format=pdf",
    );
    expect(screen.getByRole("link", { name: "Download DOCX" })).toHaveAttribute(
      "href",
      "http://localhost:8000/api/v1/tailored-resumes/t1/download?format=docx",
    );
  });

  it("shows the evidence behind each statement on request", async () => {
    stubApi({ [LATEST]: () => ({ status: 200, body: makeResume() }) });
    render(<TailoredResumePage jobId={JOB_ID} />);
    const preview = await screen.findByRole("article", { name: "Resume preview" });
    expect(within(preview).queryByText(/Evidence \(/)).toBeNull();
    fireEvent.click(screen.getByLabelText("Show the evidence behind each statement"));
    expect(
      within(preview).getByText(
        `Evidence (Machine Learning Intern at Acme Analytics): “${DOCKER}”`,
      ),
    ).toBeVisible();
  });

  it("regenerates only after confirmation", async () => {
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    const calls = stubApi({
      [LATEST]: () => ({ status: 200, body: makeResume() }),
      [GENERATE]: () => ({ status: 201, body: makeResume({ version: 2 }) }),
    });
    render(<TailoredResumePage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Regenerate" }));
    expect(confirm).toHaveBeenCalled();
    expect(calls.map((c) => c.key)).not.toContain(GENERATE);

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(await screen.findByText(/version 2/)).toBeVisible();
  });

  it("saves edits: wording, order, removals", async () => {
    const calls = stubApi({
      [LATEST]: () => ({ status: 200, body: makeResume() }),
      "PUT /tailored-resumes/t1": (body) => ({
        status: 200,
        body: makeResume({ version: 1, content: (body as { content: never }).content }),
      }),
    });
    render(<TailoredResumePage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Bullet 1", { selector: "#experience\\:w1-0" }), {
      target: { value: "Deployed ML models on AWS with Docker." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Remove skill Docker" }));
    fireEvent.click(screen.getByRole("button", { name: "Move project Budget Tracker up" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByRole("article", { name: "Resume preview" });
    const saved = calls.find((c) => c.key === "PUT /tailored-resumes/t1")?.body as {
      content: TailoredResume["content"];
    };
    expect(saved.content.experience[0].bullets[0]).toEqual({
      claim_id: "c1",
      text: "Deployed ML models on AWS with Docker.",
      evidence_ids: ["e1"],
    });
    expect(saved.content.skills.map((s) => s.text)).toEqual(["Python"]);
    expect(saved.content.projects.map((p) => p.title)).toEqual([
      "Budget Tracker",
      "Multi-Agent Research Assistant",
    ]);
  });

  it("shows why an edit was rejected, next to the statement", async () => {
    stubApi({
      [LATEST]: () => ({ status: 200, body: makeResume() }),
      "PUT /tailored-resumes/t1": () => ({
        status: 422,
        body: {
          detail: [
            {
              type: "value_error",
              loc: ["body", "experience:w1[0]"],
              msg: "“Deployed 80 ML models” isn't supported: Contains numbers or metrics not in the evidence: 80.",
            },
          ],
        },
      }),
    });
    render(<TailoredResumePage jobId={JOB_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const bullet = screen.getByLabelText("Bullet 1", { selector: "#experience\\:w1-0" });
    fireEvent.change(bullet, { target: { value: "Deployed 80 ML models with Docker on AWS." } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(bullet).toHaveAttribute("aria-invalid", "true"));
    expect(screen.getByText(/metrics not in the evidence: 80/)).toBeVisible();
    expect(screen.getByText(/aren't supported by your evidence/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled(); // still editing
  });
});
