"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Icon } from "./Icon";
import { activeSection, NAVIGATION } from "./navigation";

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
      <span className="grid h-7 w-7 place-items-center rounded-md bg-zinc-900 text-xs text-white dark:bg-zinc-100 dark:text-zinc-900">
        CP
      </span>
      <span className="text-zinc-900 dark:text-zinc-50">CareerPilot</span>
    </Link>
  );
}

function NavLinks({ current, onNavigate }: { current: string; onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="space-y-5">
      {NAVIGATION.map((group, n) => (
        <div key={group.label ?? `group-${n}`}>
          {group.label ? (
            <p className="mb-1 px-3 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase">
              {group.label}
            </p>
          ) : null}
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const active = item.href === current;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-900 aria-[current=page]:bg-zinc-900 aria-[current=page]:font-medium aria-[current=page]:text-white dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 dark:aria-[current=page]:bg-zinc-100 dark:aria-[current=page]:text-zinc-900"
                  >
                    <Icon name={item.icon} className="h-4.5 w-4.5 shrink-0" />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

/** The app frame: a sidebar on large screens, a top bar with a menu on small ones. */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "/";
  const current = activeSection(pathname);
  const [open, setOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const firstLink = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    firstLink.current?.querySelector<HTMLAnchorElement>("nav a")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        menuButton.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <a
        href="#content"
        className="sr-only z-50 rounded-md bg-white px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>

      {/* Large screens: a fixed sidebar. */}
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-64 flex-col border-r border-zinc-200 bg-white px-3 py-5 lg:flex dark:border-zinc-800 dark:bg-zinc-950">
        <div className="px-3 pb-6">
          <Brand />
        </div>
        <div className="flex-1 overflow-y-auto">
          <NavLinks current={current} />
        </div>
        <p className="px-3 pt-4 text-xs text-zinc-400">You approve every step that matters.</p>
      </aside>

      {/* Small screens: a top bar and a menu drawer. */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-zinc-200 bg-white/95 px-4 py-3 backdrop-blur lg:hidden dark:border-zinc-800 dark:bg-zinc-950/95">
        <Brand />
        <button
          ref={menuButton}
          type="button"
          onClick={() => setOpen(true)}
          aria-expanded={open}
          aria-controls="mobile-menu"
          className="rounded-md p-2 text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
        >
          <Icon name="menu" />
          <span className="sr-only">Open menu</span>
        </button>
      </header>
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close menu"
            tabIndex={-1}
            className="absolute inset-0 bg-zinc-950/40"
            onClick={() => setOpen(false)}
          />
          <div
            id="mobile-menu"
            role="dialog"
            aria-modal="true"
            aria-label="Menu"
            ref={firstLink}
            className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col overflow-y-auto bg-white px-3 py-4 shadow-xl dark:bg-zinc-950"
          >
            <div className="mb-4 flex items-center justify-between px-3">
              <Brand />
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-md p-2 text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                <Icon name="close" />
                <span className="sr-only">Close menu</span>
              </button>
            </div>
            <NavLinks current={current} onNavigate={() => setOpen(false)} />
          </div>
        </div>
      ) : null}

      <div id="content" className="flex min-w-0 flex-1 flex-col lg:pl-64">
        {children}
      </div>
    </div>
  );
}
