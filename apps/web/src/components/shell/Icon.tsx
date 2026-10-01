import type { IconName } from "./navigation";

// Simple 24px stroke icons (paths in the style of open-source line icon sets).
const PATHS: Record<IconName | "menu" | "close", string[]> = {
  home: ["M3 10.5 12 3l9 7.5", "M5 9.5V21h14V9.5"],
  user: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M4 21a8 8 0 0 1 16 0"],
  briefcase: ["M3 7h18v13H3z", "M8 7V4h8v3", "M3 12h18"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z", "m20 20-4-4"],
  star: ["m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2-5.5-2.9-5.5 2.9 1-6.2L3 9.6l6.2-.9Z"],
  compass: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z", "m15.5 8.5-2 5-5 2 2-5Z"],
  inbox: ["M3 13h5l1.5 3h5L16 13h5", "M5 4h14l2 9v7H3v-7Z"],
  file: ["M14 3H6v18h12V7Z", "M14 3v4h4", "M9 12h6", "M9 16h6"],
  mail: ["M3 5h18v14H3z", "m3 6 9 7 9-7"],
  check: ["M9 12l2 2 4-4", "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z"],
  bot: ["M5 8h14v11H5z", "M12 8V4", "M9 13h.01", "M15 13h.01", "M9 16.5h6"],
  shield: ["M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6Z", "m9 12 2 2 4-4"],
  settings: [
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
    "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z",
  ],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  close: ["M6 6l12 12", "M18 6 6 18"],
};

export function Icon({
  name,
  className = "h-5 w-5",
}: {
  name: IconName | "menu" | "close";
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
