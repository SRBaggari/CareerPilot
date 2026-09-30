import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Suggestion } from "@/lib/api/profile";

import { ExtractedInfoReview } from "./ExtractedInfoReview";
import { stubApi } from "./testApi";

afterEach(() => vi.unstubAllGlobals());

const base = {
  action: "create" as const,
  target_id: null,
  source: "resume_extraction" as const,
  rationale: "Extracted from your uploaded resume.",
  status: "pending" as const,
  resume_id: "r1",
  accepted_data: null,
  created_at: "2026-01-01T00:00:00Z",
};

const PROJECT: Suggestion = {
  ...base,
  id: "s-project",
  section: "project",
  proposed_data: {
    title: "Multi-Agent Research Assistant",
    repository_url: "https://github.com/x/mara",
    highlights: ["Implemented RAG pipeline.", "Evaluated faithfulness on 200 questions."],
  },
  source_excerpt: "Multi-Agent Research Assistant | github.com/x/mara\n• Implemented RAG pipeline.",
};
const SKILL: Suggestion = {
  ...base,
  id: "s-skill",
  section: "skill",
  proposed_data: { name: "Python", category: "programming_language" },
  source_excerpt: "Languages: Python, Java",
};

function renderReview(onChanged = vi.fn(async () => {})) {
  render(<ExtractedInfoReview count={2} onChanged={onChanged} />);
  return onChanged;
}

describe("ExtractedInfoReview", () => {
  it("presents extracted items as unconfirmed, grouped, with their source text", async () => {
    stubApi({ "GET /profile/suggestions": () => ({ status: 200, body: [PROJECT, SKILL] }) });
    renderReview();

    const panel = await screen.findByRole("region", { name: "Extracted information" });
    expect(within(panel).getByText(/is not part of your profile/i)).toBeInTheDocument();
    expect(await within(panel).findByText("Multi-Agent Research Assistant")).toBeInTheDocument();
    expect(within(panel).getByText("Evaluated faithfulness on 200 questions.")).toBeInTheDocument();
    expect(within(panel).getByText("Programming language")).toBeInTheDocument();
    expect(within(panel).getAllByText("Show text from resume")).toHaveLength(2);
    expect(within(panel).getAllByRole("button", { name: "Accept" })).toHaveLength(2);
  });

  it("accepts an item unchanged", async () => {
    const calls = stubApi({
      "GET /profile/suggestions": () => ({ status: 200, body: [SKILL] }),
      "POST /profile/suggestions/s-skill/accept": () => ({ status: 200, body: {} }),
    });
    const onChanged = renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(
      calls.find((c) => c.key === "POST /profile/suggestions/s-skill/accept")?.body,
    ).toBeUndefined();
  });

  it("lets the user edit fields and highlights before accepting", async () => {
    const calls = stubApi({
      "GET /profile/suggestions": () => ({ status: 200, body: [PROJECT] }),
      "POST /profile/suggestions/s-project/accept": () => ({ status: 200, body: {} }),
    });
    const onChanged = renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));

    fireEvent.change(screen.getByLabelText(/^title/i), { target: { value: "MARA" } });
    fireEvent.change(screen.getByLabelText(/highlights/i), {
      target: { value: "Implemented RAG pipeline.\n\n  " },
    });
    fireEvent.click(screen.getByRole("button", { name: /save & accept/i }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const body = calls.find((c) => c.key === "POST /profile/suggestions/s-project/accept")
      ?.body as {
      proposed_data: Record<string, unknown>;
    };
    expect(body.proposed_data).toMatchObject({
      title: "MARA",
      repository_url: "https://github.com/x/mara",
      highlights: ["Implemented RAG pipeline."],
    });
  });

  it("shows why an edit was rejected and keeps the form open", async () => {
    stubApi({
      "GET /profile/suggestions": () => ({ status: 200, body: [PROJECT] }),
      "POST /profile/suggestions/s-project/accept": () => ({
        status: 422,
        body: {
          detail: [
            {
              loc: ["body", "end_date"],
              msg: "Value error, end_date must not be before the start date",
            },
          ],
        },
      }),
    });
    renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: /save & accept/i }));
    expect(
      await screen.findAllByText(/end_date must not be before the start date/),
    ).not.toHaveLength(0);
    expect(screen.getByRole("button", { name: /save & accept/i })).toBeInTheDocument();
  });

  it("rejects an item", async () => {
    const calls = stubApi({
      "GET /profile/suggestions": () => ({ status: 200, body: [SKILL] }),
      "POST /profile/suggestions/s-skill/reject": () => ({ status: 200, body: {} }),
    });
    const onChanged = renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.map((c) => c.key)).toContain("POST /profile/suggestions/s-skill/reject");
  });
});
