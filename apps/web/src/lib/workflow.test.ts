import { describe, expect, it } from "vitest";

import { WORKFLOW_STEPS } from "./workflow";

describe("WORKFLOW_STEPS", () => {
  it("has unique ids", () => {
    const ids = WORKFLOW_STEPS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("requires human approval before the browser-assisted application step", () => {
    const approval = WORKFLOW_STEPS.findIndex((s) => s.id === "approval");
    const apply = WORKFLOW_STEPS.findIndex((s) => s.id === "apply");
    expect(approval).toBeGreaterThanOrEqual(0);
    expect(approval).toBeLessThan(apply);
    expect(WORKFLOW_STEPS[approval].humanGate).toBe(true);
    expect(WORKFLOW_STEPS[apply].humanGate).toBe(true);
  });
});
