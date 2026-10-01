import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { THEME_KEY } from "@/lib/theme";
import { WORKFLOW_STEPS } from "@/lib/workflow";

import { makeProfile, stubApi } from "../profile/testApi";
import { SettingsPage } from "./SettingsPage";

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});
afterEach(() => vi.unstubAllGlobals());

function stubAll(overrides: Record<string, () => { status: number; body?: unknown }> = {}) {
  return stubApi({
    "GET /profile": () => ({
      status: 200,
      body: makeProfile({ full_name: "Priya Sharma", contact_email: "priya@example.test" }),
    }),
    "GET /discovery/sources": () => ({
      status: 200,
      body: [
        { name: "mock", display_name: "Mock jobs", enabled: true, reasons: [] },
        { name: "acme", display_name: "Acme Feed", enabled: false, reasons: ["No API key."] },
      ],
    }),
    ...overrides,
  });
}

describe("SettingsPage", () => {
  it("changes and remembers the theme", async () => {
    stubAll();
    render(<SettingsPage />);
    const system = screen.getByRole("radio", { name: "Match my device" });
    expect(system).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    fireEvent.click(system);
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
    await screen.findByText("Priya Sharma");
  });

  it("shows the account, connection status and job sources", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const path = url.replace("http://localhost:8000", "");
        const bodies: Record<string, unknown> = {
          "/health": { status: "ok", service: "CareerPilot API", environment: "development" },
          "/health/ready": {
            status: "ready",
            database: { connected: true, pgvector: true, detail: null },
          },
          "/api/v1/profile": makeProfile({ full_name: "Priya Sharma" }),
          "/api/v1/discovery/sources": [
            { name: "acme", display_name: "Acme Feed", enabled: false, reasons: ["No API key."] },
          ],
        };
        return new Response(JSON.stringify(bodies[path]), { status: 200 });
      }),
    );
    render(<SettingsPage />);
    expect(await screen.findByText("Online (development)")).toBeVisible();
    expect(await screen.findByText("Connected, vector search available")).toBeVisible();
    const sources = await screen.findByRole("list", { name: "Job sources" });
    expect(sources).toHaveTextContent("Acme Feed");
    expect(sources).toHaveTextContent("No API key.");
    expect(screen.getByRole("region", { name: "Account" })).toHaveTextContent("Priya Sharma");
  });

  it("reports an unreachable API instead of failing", async () => {
    stubAll();
    render(<SettingsPage />);
    expect(await screen.findByText(/Unreachable at/)).toBeVisible();
  });

  it("explains the safety guarantees and the workflow, with human gates", async () => {
    stubAll();
    render(<SettingsPage />);
    expect(screen.getByRole("region", { name: "Safety and privacy" })).toHaveTextContent(
      "Nothing is submitted without your explicit approval",
    );
    const steps = within(screen.getByRole("list", { name: /careerpilot workflow/i })).getAllByRole(
      "listitem",
    );
    expect(steps.map((s) => s.textContent)).toEqual(
      WORKFLOW_STEPS.map((step) => expect.stringContaining(step.title) as unknown as string),
    );
    expect(screen.getAllByText(/requires approval/i)).toHaveLength(
      WORKFLOW_STEPS.filter((s) => s.humanGate).length,
    );
    await screen.findByText("Priya Sharma");
  });
});
