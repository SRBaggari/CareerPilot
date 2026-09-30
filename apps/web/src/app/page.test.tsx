import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "./page";
import { WORKFLOW_STEPS } from "@/lib/workflow";

describe("Landing page", () => {
  it("renders the product headline", () => {
    render(<Home />);
    expect(
      screen.getByRole("heading", { level: 1, name: /personal ai co-pilot/i }),
    ).toBeInTheDocument();
  });

  it("lists every workflow step in order", () => {
    render(<Home />);
    const list = screen.getByRole("list", { name: /careerpilot workflow/i });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(WORKFLOW_STEPS.length);
    items.forEach((item, i) => {
      expect(item).toHaveTextContent(WORKFLOW_STEPS[i].title);
    });
  });

  it("marks approval and application steps as human-gated", () => {
    render(<Home />);
    expect(screen.getAllByText(/requires approval/i)).toHaveLength(
      WORKFLOW_STEPS.filter((s) => s.humanGate).length,
    );
  });
});
