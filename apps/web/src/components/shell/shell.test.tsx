import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";
import { activeSection, NAVIGATION } from "./navigation";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const SECTIONS = [
  "Dashboard",
  "Profile",
  "Jobs",
  "Job Analysis",
  "Recommended Jobs",
  "Applications",
  "Resume Builder",
  "Cover Letters",
  "Application Review",
  "Settings",
];

beforeEach(() => {
  pathname = "/";
});

describe("navigation", () => {
  it("has every section", () => {
    const labels = NAVIGATION.flatMap((g) => g.items.map((i) => i.label));
    for (const section of SECTIONS) expect(labels).toContain(section);
  });

  it("highlights the section a page belongs to", () => {
    expect(activeSection("/")).toBe("/");
    expect(activeSection("/jobs")).toBe("/jobs");
    expect(activeSection("/jobs/j1")).toBe("/jobs");
    expect(activeSection("/jobs/j1/match")).toBe("/jobs");
    expect(activeSection("/jobs/j1/resume")).toBe("/resumes");
    expect(activeSection("/jobs/j1/cover-letter")).toBe("/cover-letters");
    expect(activeSection("/applications/a1")).toBe("/applications");
    expect(activeSection("/applications/a1/review")).toBe("/review");
    expect(activeSection("/agent/r1")).toBe("/agent");
    expect(activeSection("/settings")).toBe("/settings");
  });
});

describe("AppShell", () => {
  it("links to every section and marks the current one", () => {
    pathname = "/jobs/j1/resume";
    render(<AppShell>page</AppShell>);
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const section of SECTIONS)
      expect(within(nav).getByRole("link", { name: section })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Resume Builder" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Jobs" })).not.toHaveAttribute("aria-current");
    expect(screen.getByText("page")).toBeInTheDocument();
  });

  it("opens and closes the mobile menu", () => {
    render(<AppShell>page</AppShell>);
    const open = screen.getByRole("button", { name: "Open menu" });
    expect(open).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();

    fireEvent.click(open);
    const menu = screen.getByRole("dialog", { name: "Menu" });
    expect(open).toHaveAttribute("aria-expanded", "true");
    expect(within(menu).getByRole("link", { name: "Dashboard" })).toHaveFocus();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();
    expect(open).toHaveFocus();

    fireEvent.click(open);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("link", { name: "Settings" }));
    expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull(); // closes on navigation
  });

  it("keeps focus inside the open menu and returns it however the menu closes", () => {
    render(<AppShell>page</AppShell>);
    const open = screen.getByRole("button", { name: "Open menu" });
    fireEvent.click(open);
    const menu = screen.getByRole("dialog", { name: "Menu" });
    const focusable = [...menu.querySelectorAll<HTMLElement>("a[href], button")];
    const [first, last] = [focusable[0], focusable[focusable.length - 1]];
    // Tab from the last item wraps to the first, Shift+Tab from the first to the last.
    last.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
    // The page behind is inert and doesn't scroll while the menu is open.
    expect(document.getElementById("content")).toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(within(menu).getByRole("button", { name: "Close menu" }));
    expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();
    expect(open).toHaveFocus();
    expect(document.getElementById("content")).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("");
  });

  it("offers a skip link to the content", () => {
    render(<AppShell>page</AppShell>);
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute(
      "href",
      "#content",
    );
  });
});
