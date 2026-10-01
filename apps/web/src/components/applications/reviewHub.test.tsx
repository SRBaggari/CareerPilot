import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApplicationSummary } from "@/lib/api/applications";

import { stubApi } from "../profile/testApi";
import { ReviewHubPage } from "./ReviewHubPage";

afterEach(() => vi.unstubAllGlobals());

const app = (id: string, overrides: Partial<ApplicationSummary>) =>
  ({
    id,
    company: "Northwind",
    position: `Role ${id}`,
    status: "awaiting_approval",
    approval_state: "ready_for_review",
    updated_at: "2026-09-30T10:00:00Z",
    ...overrides,
  }) as ApplicationSummary;

describe("ReviewHubPage", () => {
  it("groups applications by the decision they need", async () => {
    stubApi({
      "GET /applications?sort=updated": () => ({
        status: 200,
        body: [
          app("a1", {}),
          app("a2", { approval_state: "approved" }),
          app("a3", { approval_state: "draft", status: "application_prepared" }),
          app("a4", { approval_state: "draft", status: "saved" }), // not prepared: not listed
          app("a5", { approval_state: "submitted", status: "submitted" }),
        ],
      }),
    });
    render(<ReviewHubPage />);
    const review = await screen.findByRole("region", { name: /Ready for your review \(1\)/ });
    expect(
      within(review).getByRole("link", { name: "Review Role a1 at Northwind" }),
    ).toHaveAttribute("href", "/applications/a1/review");
    expect(
      screen.getByRole("region", { name: /Approved, not yet submitted \(1\)/ }),
    ).toHaveTextContent("Role a2");
    expect(screen.getByRole("region", { name: /Being prepared \(1\)/ })).toHaveTextContent(
      "Role a3",
    );
    expect(screen.queryByText("Role a4")).toBeNull();
    expect(screen.queryByText("Role a5")).toBeNull();
    expect(screen.queryByRole("region", { name: /Rejected/ })).toBeNull();
  });

  it("says when nothing is waiting", async () => {
    stubApi({ "GET /applications?sort=updated": () => ({ status: 200, body: [] }) });
    render(<ReviewHubPage />);
    expect(await screen.findByText("Nothing waiting for your review.")).toBeVisible();
  });
});
