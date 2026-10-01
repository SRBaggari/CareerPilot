"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState, inputClass } from "@/components/ui";
import {
  changeStatus,
  formatDate,
  formatDateTime,
  getDashboard,
  listApplications,
  STATUS_LABELS,
  STATUSES,
  updateFollowUp,
  type ApplicationFilters,
  type ApplicationStatus,
  type ApplicationSummary,
  type Dashboard,
} from "@/lib/api/applications";
import { ApiError } from "@/lib/api/client";

const BOARD_COLUMNS = STATUSES;
const EMPTY: ApplicationFilters = { statuses: [], q: "", followUpDue: false, sort: "updated" };

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950">
      <p className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">{value}</p>
      <p className="text-xs text-zinc-500">{label}</p>
    </div>
  );
}

/** Follow-up reminders and upcoming interviews across all applications. */
function Reminders({ dashboard, onDone }: { dashboard: Dashboard; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function complete(appId: string, followUpId: string, status: "done" | "skipped") {
    setError(null);
    try {
      await updateFollowUp(appId, followUpId, { status });
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't update the follow-up.");
    }
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card
        title="Follow-up reminders"
        description="Overdue or due within a week. CareerPilot never sends these for you."
      >
        {error ? (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        ) : null}
        {dashboard.follow_ups_due.length ? (
          <ul className="space-y-2" aria-label="Follow-ups due">
            {dashboard.follow_ups_due.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                  {f.overdue ? <Badge tone="ai">Overdue</Badge> : null}{" "}
                  <Link
                    href={`/applications/${f.application_id}`}
                    className="font-medium hover:underline"
                  >
                    {f.subject}
                  </Link>{" "}
                  <span className="text-zinc-500">
                    · {f.company} · due {formatDate(f.due_at)}
                  </span>
                </span>
                <span className="flex flex-wrap gap-1">
                  <Button size="sm" onClick={() => void complete(f.application_id, f.id, "done")}>
                    Done
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void complete(f.application_id, f.id, "skipped")}
                  >
                    Skip
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>No follow-ups due this week.</EmptyState>
        )}
      </Card>
      <Card title="Upcoming interviews" description="The next two weeks.">
        {dashboard.upcoming_interviews.length ? (
          <ul className="space-y-2 text-sm" aria-label="Upcoming interviews">
            {dashboard.upcoming_interviews.map((i) => (
              <li key={i.id}>
                <Link
                  href={`/applications/${i.application_id}`}
                  className="font-medium hover:underline"
                >
                  {i.company}: {i.position}
                </Link>
                <span className="block text-zinc-500">
                  {formatDateTime(i.scheduled_at)} · {i.interview_type.replace("_", " ")}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>No interviews scheduled.</EmptyState>
        )}
      </Card>
    </div>
  );
}

function ApplicationCard({
  app,
  onMove,
}: {
  app: ApplicationSummary;
  onMove: (app: ApplicationSummary, status: ApplicationStatus) => void;
}) {
  return (
    <li
      aria-label={`${app.position} at ${app.company}`}
      className="space-y-1 rounded-lg border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-950"
    >
      <Link
        href={`/applications/${app.id}`}
        className="font-medium text-zinc-900 hover:underline dark:text-zinc-50"
      >
        {app.position}
      </Link>
      <p className="text-zinc-600 dark:text-zinc-400">{app.company}</p>
      <p className="flex flex-wrap gap-1 text-xs">
        {app.overdue_follow_ups ? <Badge tone="ai">Follow-up overdue</Badge> : null}
        {app.next_interview_at ? (
          <Badge tone="lock">Interview {formatDate(app.next_interview_at)}</Badge>
        ) : null}
        {app.applied_at ? <Badge>Applied {formatDate(app.applied_at)}</Badge> : null}
      </p>
      <label className="block text-xs text-zinc-500">
        <span className="sr-only">
          Move {app.position} at {app.company} to
        </span>
        <select
          className={`${inputClass} mt-1 py-1 text-xs`}
          value={app.status}
          onChange={(e) => onMove(app, e.target.value as ApplicationStatus)}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </select>
      </label>
    </li>
  );
}

export function ApplicationsPage() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [apps, setApps] = useState<ApplicationSummary[] | null>(null);
  const [filters, setFilters] = useState<ApplicationFilters>(EMPTY);
  const [view, setView] = useState<"board" | "list">("board");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (applied: ApplicationFilters) => {
    const [board, found] = await Promise.all([getDashboard(), listApplications(applied)]);
    return { board, found };
  }, []);

  useEffect(() => {
    let cancelled = false;
    load(filters).then(
      ({ board, found }) => {
        if (cancelled) return;
        setDashboard(board);
        setApps(found);
      },
      (e: unknown) =>
        !cancelled && setError(e instanceof ApiError ? e.message : "Could not load applications."),
    );
    return () => {
      cancelled = true;
    };
  }, [filters, load]);

  async function reload() {
    const { board, found } = await load(filters);
    setDashboard(board);
    setApps(found);
  }

  async function move(app: ApplicationSummary, status: ApplicationStatus) {
    setError(null);
    try {
      await changeStatus(app.id, status);
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't move the application.");
    }
  }

  const toggleStatus = (s: ApplicationStatus) =>
    setFilters({
      ...filters,
      statuses: filters.statuses.includes(s)
        ? filters.statuses.filter((x) => x !== s)
        : [...filters.statuses, s],
    });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Applications
        </h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Track every application from discovery to offer. CareerPilot never submits anything: you
          approve each application and record when you submitted it.
        </p>
      </div>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      ) : null}

      {dashboard ? (
        <>
          <section aria-label="Summary" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Tracked" value={dashboard.total} />
            <Stat label="Active" value={dashboard.active} />
            <Stat
              label="Submitted or later"
              value={
                dashboard.counts.submitted +
                dashboard.counts.assessment +
                dashboard.counts.interview +
                dashboard.counts.offer
              }
            />
            <Stat label="Offers" value={dashboard.counts.offer} />
          </section>
          <Reminders dashboard={dashboard} onDone={() => void reload()} />
        </>
      ) : null}

      <Card title="Find applications">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-3">
            <label className="flex-1 text-sm">
              <span className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Search
              </span>
              <input
                type="search"
                className={inputClass}
                placeholder="Company, position, location or notes"
                value={filters.q}
                onChange={(e) => setFilters({ ...filters, q: e.target.value })}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Sort by
              </span>
              <select
                className={inputClass}
                value={filters.sort}
                onChange={(e) =>
                  setFilters({ ...filters, sort: e.target.value as ApplicationFilters["sort"] })
                }
              >
                <option value="updated">Recently updated</option>
                <option value="company">Company</option>
                <option value="discovered">Date discovered</option>
                <option value="applied">Date applied</option>
              </select>
            </label>
          </div>
          <fieldset>
            <legend className="mb-1 text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Status
            </legend>
            <div className="flex flex-wrap gap-1">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={filters.statuses.includes(s)}
                  onClick={() => toggleStatus(s)}
                  className="rounded-full border border-zinc-300 px-2.5 py-0.5 text-xs aria-pressed:border-zinc-900 aria-pressed:bg-zinc-900 aria-pressed:text-white dark:border-zinc-700 dark:aria-pressed:bg-zinc-100 dark:aria-pressed:text-zinc-900"
                >
                  {STATUS_LABELS[s]} {dashboard ? dashboard.counts[s] : ""}
                </button>
              ))}
            </div>
          </fieldset>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={filters.followUpDue}
              onChange={(e) => setFilters({ ...filters, followUpDue: e.target.checked })}
            />
            Only with a follow-up due this week
          </label>
        </div>
      </Card>

      <div className="flex flex-wrap gap-1" role="group" aria-label="View">
        {(["board", "list"] as const).map((v) => (
          <Button
            key={v}
            size="sm"
            variant={view === v ? "primary" : "secondary"}
            aria-pressed={view === v}
            onClick={() => setView(v)}
          >
            {v === "board" ? "Board" : "List"}
          </Button>
        ))}
      </div>

      {apps === null && !error ? (
        <p role="status" className="text-sm text-zinc-500">
          Loading…
        </p>
      ) : null}
      {apps && apps.length === 0 ? (
        <EmptyState>
          No applications
          {filters.q || filters.statuses.length || filters.followUpDue
            ? " match these filters"
            : " yet"}
          . Track one from a job&apos;s page or from your{" "}
          <Link href="/recommendations" className="underline">
            recommendations
          </Link>
          .
        </EmptyState>
      ) : null}

      {apps && apps.length > 0 && view === "board" ? (
        <div className="relative flex gap-3 overflow-x-auto pb-2" aria-label="Status board">
          {BOARD_COLUMNS.filter(
            (s) => filters.statuses.length === 0 || filters.statuses.includes(s),
          ).map((s) => {
            const column = apps.filter((a) => a.status === s);
            return (
              <section
                key={s}
                aria-label={STATUS_LABELS[s]}
                className="w-60 shrink-0 space-y-2 rounded-xl bg-zinc-100 p-2 dark:bg-zinc-900"
              >
                <h2 className="px-1 text-xs font-semibold text-zinc-600 uppercase dark:text-zinc-400">
                  {STATUS_LABELS[s]} <span className="font-normal">{column.length}</span>
                </h2>
                <ul className="space-y-2">
                  {column.map((a) => (
                    <ApplicationCard key={a.id} app={a} onMove={(app, to) => void move(app, to)} />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      ) : null}

      {apps && apps.length > 0 && view === "list" ? (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left text-sm" aria-label="Applications">
            <thead className="text-xs text-zinc-500 uppercase">
              <tr>
                <th className="py-2">Position</th>
                <th>Company</th>
                <th>Status</th>
                <th>Discovered</th>
                <th>Applied</th>
                <th>Next</th>
              </tr>
            </thead>
            <tbody>
              {apps.map((a) => (
                <tr key={a.id} className="border-t border-zinc-200 dark:border-zinc-800">
                  <td className="py-2">
                    <Link href={`/applications/${a.id}`} className="font-medium hover:underline">
                      {a.position}
                    </Link>
                  </td>
                  <td>{a.company}</td>
                  <td>{STATUS_LABELS[a.status]}</td>
                  <td>{formatDate(a.discovered_at)}</td>
                  <td>{formatDate(a.applied_at)}</td>
                  <td className="text-xs">
                    {a.next_interview_at
                      ? `Interview ${formatDate(a.next_interview_at)}`
                      : a.next_follow_up_at
                        ? `Follow up ${formatDate(a.next_follow_up_at)}`
                        : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
