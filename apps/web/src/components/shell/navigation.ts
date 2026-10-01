/** The app's sections, grouped as they appear in the sidebar. */

export type IconName =
  | "home"
  | "user"
  | "briefcase"
  | "search"
  | "star"
  | "compass"
  | "inbox"
  | "file"
  | "mail"
  | "check"
  | "bot"
  | "shield"
  | "settings";

export type NavItem = { href: string; label: string; icon: IconName };
export type NavGroup = { label: string | null; items: NavItem[] };

export const NAVIGATION: NavGroup[] = [
  {
    label: null,
    items: [
      { href: "/", label: "Dashboard", icon: "home" },
      { href: "/profile", label: "Profile", icon: "user" },
    ],
  },
  {
    label: "Jobs",
    items: [
      { href: "/jobs", label: "Jobs", icon: "briefcase" },
      { href: "/analyze", label: "Job Analysis", icon: "search" },
      { href: "/recommendations", label: "Recommended Jobs", icon: "star" },
      { href: "/discover", label: "Discover", icon: "compass" },
    ],
  },
  {
    label: "Applications",
    items: [
      { href: "/applications", label: "Applications", icon: "inbox" },
      { href: "/resumes", label: "Resume Builder", icon: "file" },
      { href: "/cover-letters", label: "Cover Letters", icon: "mail" },
      { href: "/review", label: "Application Review", icon: "check" },
      { href: "/agent", label: "Agent", icon: "bot" },
    ],
  },
  {
    label: null,
    items: [
      { href: "/verify", label: "Check claims", icon: "shield" },
      { href: "/settings", label: "Settings", icon: "settings" },
    ],
  },
];

// Pages inside a section, mapped to the section they belong to (most specific first).
const SECTIONS: [RegExp, string][] = [
  [/^\/jobs\/[^/]+\/resume/, "/resumes"],
  [/^\/jobs\/[^/]+\/cover-letter/, "/cover-letters"],
  [/^\/applications\/[^/]+\/review/, "/review"],
  [/^\/jobs(\/|$)/, "/jobs"],
  [/^\/applications(\/|$)/, "/applications"],
  [/^\/agent(\/|$)/, "/agent"],
];

/** The sidebar entry to highlight for a path. */
export function activeSection(pathname: string): string {
  for (const [pattern, href] of SECTIONS) if (pattern.test(pathname)) return href;
  const items = NAVIGATION.flatMap((g) => g.items);
  const match = items.find((i) => i.href !== "/" && pathname.startsWith(i.href));
  return match ? match.href : pathname === "/" ? "/" : "";
}
