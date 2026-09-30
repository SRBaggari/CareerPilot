import Link from "next/link";

const LINKS = [
  { href: "/profile", label: "Profile" },
  { href: "/jobs", label: "Jobs" },
] as const;

export function AppHeader({ current }: { current: (typeof LINKS)[number]["href"] }) {
  return (
    <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/90 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/90">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
        <Link href="/" className="font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          CareerPilot
        </Link>
        <nav aria-label="Main">
          <ul className="flex gap-1 text-sm">
            {LINKS.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  aria-current={link.href === current ? "page" : undefined}
                  className="rounded-md px-3 py-1.5 text-zinc-600 hover:bg-zinc-100 aria-[current=page]:bg-zinc-100 aria-[current=page]:font-medium aria-[current=page]:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:aria-[current=page]:bg-zinc-900 dark:aria-[current=page]:text-zinc-100"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}
