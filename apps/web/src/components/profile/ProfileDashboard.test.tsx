import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProfileDashboard } from "./ProfileDashboard";
import { makeProfile, stubApi } from "./testApi";

afterEach(() => vi.unstubAllGlobals());

describe("ProfileDashboard", () => {
  it("offers to create a profile when none exists, then shows it", async () => {
    const calls = stubApi({
      "GET /profile": () => ({ status: 404, body: { detail: "Profile not found." } }),
      "POST /profile": () => ({ status: 201, body: makeProfile({ headline: "ML engineer" }) }),
    });
    render(<ProfileDashboard />);

    expect(
      await screen.findByRole("heading", { name: /create your master profile/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /create profile/i }));
    expect(await screen.findByText("Full name is required")).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /profile")).toBe(false);

    fireEvent.change(screen.getByLabelText(/full name/i), {
      target: { value: "  Test Candidate " },
    });
    fireEvent.click(screen.getByRole("button", { name: /create profile/i }));

    expect(await screen.findByText("ML engineer")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /profile")?.body).toMatchObject({
      full_name: "Test Candidate",
      headline: null,
    });
  });

  it("shows server validation errors next to the field", async () => {
    stubApi({
      "GET /profile": () => ({ status: 404, body: { detail: "Profile not found." } }),
      "POST /profile": () => ({
        status: 422,
        body: {
          detail: [
            { loc: ["body", "contact_email"], msg: "Value error, must be a valid email address" },
          ],
        },
      }),
    });
    render(<ProfileDashboard />);
    fireEvent.change(await screen.findByLabelText(/full name/i), { target: { value: "A" } });
    fireEvent.change(screen.getByLabelText(/contact email/i), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: /create profile/i }));
    expect(await screen.findByText("must be a valid email address")).toBeInTheDocument();
    expect(screen.getByLabelText(/contact email/i)).toHaveAttribute("aria-invalid", "true");
  });

  it("renders sections with their highlights and deletes an item after confirmation", async () => {
    let profile = makeProfile({
      projects: [
        {
          id: "proj1",
          sort_order: 0,
          title: "Multi-Agent Research Assistant",
          role: "Lead",
          evidence: [
            {
              id: "ev1",
              source_type: "project",
              subject_id: "proj1",
              content: "Implemented RAG pipeline using document retrieval and QA.",
              origin: "resume_extracted",
              confirmed_at: "2026-01-01T00:00:00Z",
              is_cited: false,
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ],
        },
      ],
    });
    const calls = stubApi({
      "GET /profile": () => ({ status: 200, body: profile }),
      "DELETE /profile/projects/proj1": () => {
        profile = makeProfile();
        return { status: 204 };
      },
    });
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    render(<ProfileDashboard />);

    const projects = await screen.findByRole("region", { name: "Projects" });
    expect(within(projects).getByText(/Implemented RAG pipeline/)).toBeInTheDocument();
    expect(within(projects).getByText(/from your resume · approved by you/i)).toBeInTheDocument();

    fireEvent.click(within(projects).getByRole("button", { name: /delete multi-agent/i }));
    await waitFor(() =>
      expect(within(projects).getByText(/no projects added yet/i)).toBeInTheDocument(),
    );
    expect(calls.map((c) => c.key)).toContain("DELETE /profile/projects/proj1");
  });

  it("shows a helpful error when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<ProfileDashboard />);
    expect(await screen.findByText(/cannot reach the careerpilot api/i)).toBeInTheDocument();
  });
});
