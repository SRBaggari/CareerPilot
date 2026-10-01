"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card, EmptyState } from "@/components/ui";
import {
  formatDate,
  listApplications,
  STATUS_LABELS,
  type ApplicationSummary,
} from "@/lib/api/applications";
import { ApiError } from "@/lib/api/client";

const GROUPS = [
  {
    id: "to-review",
    title: "Ready for your review",
    description: "Check everything that will be submitted, then approve or reject it.",
    match: (a: ApplicationSummary) => a.approval_state === "ready_for_review",
    action: "Review",
  },
  {
    id: "approved",
    title: "Approved, not yet submitted",
    description: "Submit it yourself and record it, or use browser assistance.",
    match: (a: ApplicationSummary) => a.approval_state === "approved",
    action: "Open",
  },
  {
    id: "preparing",
    title: "Being prepared",
    description: "Prepared applications you haven't sent for review yet.",
    match: (a: ApplicationSummary) =>
      a.approval_state === "draft" &&
      (a.status === "application_prepared" || a.status === "awaiting_approval"),
    action: "Open",
  },
  {
    id: "rejected",
    title: "Rejected by you",
    description: "Fix what was wrong, then send it for review again.",
    match: (a: ApplicationSummary) => a.approval_state === "rejected",
    action: "Open",
  },
] as const;

/** Application Review: every application that needs (or had) your decision. */
export function ReviewHubPage() {
  const [apps, setApps] = useState<ApplicationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listApplications({ statuses: [], q: "", followUpDue: false, sort: "updated" }).then(
      (found) => !cancelled && setApps(found),
      (e: unknown) =>
        !cancelled &&
        setError(e instanceof ApiError ? e.message : "Could not load your applications."),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Application Review
        </h1>
        <p className="max-w-3xl text-sm text-zinc-600 dark:text-zinc-400">
          Before anything is submitted you see the job, your resume, cover letter, answers, personal
          information and verification results, and approve that exact version. Opening a review
          never approves it.
        </p>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      ) : apps === null ? (
        <p role="status" className="text-sm text-zinc-500">
          Loading…
        </p>
      ) : (
        GROUPS.map((group) => {
          const items = apps.filter(group.match);
          if (group.id !== "to-review" && items.length === 0) return null;
          return (
            <Card
              key={group.id}
              id={group.id}
              title={`${group.title} (${items.length})`}
              description={group.description}
            >
              {items.length === 0 ? (
                <EmptyState>Nothing waiting for your review.</EmptyState>
              ) : (
                <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {items.map((a) => (
                    <li
                      key={a.id}
                      className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium text-zinc-900 dark:text-zinc-100">
                          {a.position}
                        </p>
                        <p className="text-sm text-zinc-500">
                          {a.company} · {STATUS_LABELS[a.status]} · updated{" "}
                          {formatDate(a.updated_at)}
                        </p>
                      </div>
                      <Link
                        href={`/applications/${a.id}/review`}
                        className="text-sm font-medium underline"
                        aria-label={`${group.action} ${a.position} at ${a.company}`}
                      >
                        {group.action}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          );
        })
      )}
    </main>
  );
}
