"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card, EmptyState } from "@/components/ui";
import { listRuns, STAGE_LABELS, type AgentRun } from "@/lib/api/agent";
import {
  formatDate,
  formatDateTime,
  getDashboard,
  STATUS_LABELS,
  type ApplicationStatus,
  type Dashboard,
} from "@/lib/api/applications";
import { ApiError } from "@/lib/api/client";
import { listJobs, type JobSummary } from "@/lib/api/jobs";
import { getProfile, type Profile } from "@/lib/api/profile";

const SUBMITTED: ApplicationStatus[] = ["submitted", "assessment", "interview", "offer"];

export type Activity = { at: string; title: string; detail: string; href: string };

/** The latest things that happened, across jobs, applications and agent runs. */
export function recentActivity(
  jobs: JobSummary[],
  dashboard: Dashboard | null,
  runs: AgentRun[],
  limit = 8,
): Activity[] {
  const items: Activity[] = [
    ...jobs.map((j) => ({
      at: j.created_at,
      title: `Added ${j.title}`,
      detail: j.company_name,
      href: `/jobs/${j.id}`,
    })),
    ...(dashboard?.recent ?? []).map((a) => ({
      at: a.updated_at,
      title: `${a.position}: ${STATUS_LABELS[a.status]}`,
      detail: a.company,
      href: `/applications/${a.id}`,
    })),
    ...runs.map((r) => ({
      at: r.updated_at,
      title: `Agent ${r.status === "waiting_for_human" ? "waiting for you" : r.status.replace(/_/g, " ")}`,
      detail: `At ${STAGE_LABELS[r.stage]}`,
      href: `/agent/${r.id}`,
    })),
  ];
  return items.sort((a, b) => b.at.localeCompare(a.at)).slice(0, limit);
}

type Stat = { label: string; value: number; hint: string; href: string };

export function dashboardStats(jobs: JobSummary[], d: Dashboard | null): Stat[] {
  const count = (statuses: ApplicationStatus[]) =>
    statuses.reduce((sum, s) => sum + (d?.counts[s] ?? 0), 0);
  const overdue = d?.follow_ups_due.filter((f) => f.overdue).length ?? 0;
  return [
    { label: "Jobs discovered", value: jobs.length, hint: "found or added", href: "/jobs" },
    { label: "Saved jobs", value: count(["saved"]), hint: "to consider", href: "/applications" },
    {
      label: "Applications prepared",
      value: count(["application_prepared", "awaiting_approval"]),
      hint: `${count(["awaiting_approval"])} awaiting approval`,
      href: "/review",
    },
    {
      label: "Applications submitted",
      value: count(SUBMITTED),
      hint: "including later stages",
      href: "/applications",
    },
    {
      label: "Interviews",
      value: count(["interview"]),
      hint: `${d?.upcoming_interviews.length ?? 0} upcoming`,
      href: "/applications",
    },
    {
      label: "Follow-ups",
      value: d?.follow_ups_due.length ?? 0,
      hint: overdue ? `${overdue} overdue` : "due this week",
      href: "/applications",
    },
  ];
}

function StatTile({ stat }: { stat: Stat }) {
  return (
    <Link
      href={stat.href}
      className="rounded-xl border border-zinc-200 bg-white p-4 transition-colors hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950 dark:hover:border-zinc-700 dark:hover:bg-zinc-900"
    >
      <p className="text-sm text-zinc-500 dark:text-zinc-400">{stat.label}</p>
      <p className="mt-1 text-3xl font-semibold tracking-tight text-zinc-900 tabular-nums dark:text-zinc-50">
        {stat.value}
      </p>
      <p className="mt-1 text-xs text-zinc-500">{stat.hint}</p>
    </Link>
  );
}

const actionClass =
  "inline-flex items-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors";

export function DashboardPage() {
  const [profile, setProfile] = useState<Profile | null | undefined>(undefined);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProfile().then(
      async (found) => {
        if (cancelled) return;
        setProfile(found);
        if (!found) return;
        const [j, d, r] = await Promise.allSettled([listJobs(), getDashboard(), listRuns()]);
        if (cancelled) return;
        if (j.status === "fulfilled") setJobs(j.value);
        if (d.status === "fulfilled") setDashboard(d.value);
        if (r.status === "fulfilled") setRuns(r.value);
        if ([j, d, r].some((x) => x.status === "rejected"))
          setError("Some of your dashboard couldn't be loaded. Refresh to try again.");
      },
      (e: unknown) => {
        if (cancelled) return;
        setProfile(null);
        setError(e instanceof ApiError ? e.message : "Could not reach CareerPilot.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  if (profile === undefined) {
    return (
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <p role="status" className="text-sm text-zinc-500">
          Loading…
        </p>
      </main>
    );
  }

  const waiting = runs.filter((r) => r.status === "waiting_for_human");
  const awaiting = (dashboard?.recent ?? []).filter((a) => a.approval_state === "ready_for_review");
  const activity = recentActivity(jobs, dashboard, runs);

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            {profile
              ? `Welcome back, ${profile.full_name.split(" ")[0]}`
              : "Welcome to CareerPilot"}
          </h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Your job search at a glance. Nothing is submitted without your approval.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href="/analyze"
            className={`${actionClass} bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300`}
          >
            Analyze a job
          </Link>
          <Link
            href="/recommendations"
            className={`${actionClass} border border-zinc-300 bg-white text-zinc-800 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800`}
          >
            Recommended jobs
          </Link>
        </div>
      </div>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      ) : null}

      {profile === null ? (
        <Card id="get-started" title="Get started">
          <ol className="list-decimal space-y-1 pl-5 text-sm text-zinc-700 dark:text-zinc-300">
            <li>
              <Link href="/profile" className="font-medium underline">
                Create your profile
              </Link>{" "}
              and upload your resume: everything CareerPilot writes comes from it.
            </li>
            <li>Analyze a job, or let CareerPilot recommend some.</li>
            <li>Prepare, review and approve your application.</li>
          </ol>
        </Card>
      ) : (
        <>
          <section
            aria-label="Summary"
            className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6"
          >
            {dashboardStats(jobs, dashboard).map((s) => (
              <StatTile key={s.label} stat={s} />
            ))}
          </section>

          <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <div className="space-y-6">
              <Card id="attention" title="Needs your attention">
                {awaiting.length + waiting.length + (dashboard?.follow_ups_due.length ?? 0) ===
                0 ? (
                  <EmptyState>You&apos;re all caught up.</EmptyState>
                ) : (
                  <ul className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
                    {awaiting.map((a) => (
                      <li key={a.id} className="flex flex-wrap justify-between gap-2 py-2">
                        <span>
                          Review and approve <strong>{a.position}</strong> at {a.company}
                        </span>
                        <Link
                          href={`/applications/${a.id}/review`}
                          className="font-medium underline"
                        >
                          Review
                        </Link>
                      </li>
                    ))}
                    {waiting.map((r) => (
                      <li key={r.id} className="flex flex-wrap justify-between gap-2 py-2">
                        <span>
                          The agent is waiting for you at <strong>{STAGE_LABELS[r.stage]}</strong>
                          {r.pause ? `: ${r.pause.message}` : ""}
                        </span>
                        <Link href={`/agent/${r.id}`} className="font-medium underline">
                          Open
                        </Link>
                      </li>
                    ))}
                    {(dashboard?.follow_ups_due ?? []).map((f) => (
                      <li key={f.id} className="flex flex-wrap justify-between gap-2 py-2">
                        <span>
                          {f.overdue ? <strong className="text-red-700">Overdue: </strong> : null}
                          {f.subject ?? "Follow up"} · {f.position} at {f.company}
                        </span>
                        <span className="text-zinc-500">{formatDate(f.due_at)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
              <Card id="interviews" title="Upcoming interviews">
                {dashboard?.upcoming_interviews.length ? (
                  <ul className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
                    {dashboard.upcoming_interviews.map((i) => (
                      <li key={i.id} className="flex flex-wrap justify-between gap-2 py-2">
                        <Link href={`/applications/${i.application_id}`} className="underline">
                          {i.position} at {i.company}
                        </Link>
                        <span className="text-zinc-500">
                          {i.interview_type.replace(/_/g, " ")} · {formatDateTime(i.scheduled_at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState>No interviews scheduled in the next two weeks.</EmptyState>
                )}
              </Card>
            </div>
            <Card id="activity" title="Recent activity">
              {activity.length ? (
                <ol className="space-y-3 text-sm" aria-label="Recent activity">
                  {activity.map((a) => (
                    <li key={`${a.href}-${a.at}`} className="flex gap-3">
                      <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-zinc-300 dark:bg-zinc-600" />
                      <div className="min-w-0">
                        <Link
                          href={a.href}
                          className="font-medium text-zinc-900 hover:underline dark:text-zinc-100"
                        >
                          {a.title}
                        </Link>
                        <p className="text-xs text-zinc-500">
                          {a.detail} · {formatDateTime(a.at)}
                        </p>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <EmptyState>Nothing yet. Analyze a job to get started.</EmptyState>
              )}
            </Card>
          </div>
        </>
      )}
    </main>
  );
}
