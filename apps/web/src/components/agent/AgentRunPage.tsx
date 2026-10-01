"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import {
  advanceRun,
  cancelRun,
  getRun,
  PAUSE_LABELS,
  STAGE_LABELS,
  STATUS_LABELS,
  type AgentRun,
} from "@/lib/api/agent";
import { formatDateTime } from "@/lib/api/applications";
import { ApiError } from "@/lib/api/client";

/** Where the candidate acts on each kind of pause. */
function NextStep({ run }: { run: AgentRun }) {
  const app = run.application_id;
  const job = run.job_id;
  const links: { href: string; label: string }[] = [];
  if (run.pause?.kind === "missing_information") links.push({ href: "/profile", label: "Profile" });
  if (run.pause?.kind === "verification_failed" && job)
    links.push(
      { href: `/jobs/${job}/resume`, label: "Tailored resume" },
      { href: `/jobs/${job}/cover-letter`, label: "Cover letter" },
    );
  if (
    (run.pause?.kind === "ambiguous_fields" || run.stage === "review") &&
    job &&
    run.pause?.kind !== "missing_information"
  )
    links.push({ href: `/jobs/${job}/questions`, label: "Application questions" });
  if (app && (run.stage === "approve" || run.stage === "review"))
    links.push({ href: `/applications/${app}/review`, label: "Review and approve" });
  if (app && run.stage === "submit")
    links.push(
      { href: `/applications/${app}`, label: "Record the submission" },
      { href: `/applications/${app}/assist`, label: "Browser assistance" },
    );
  if (!links.length) return null;
  return (
    <p className="mt-3 flex flex-wrap gap-3 text-sm">
      {links.map((l) => (
        <Link key={l.href} href={l.href} className="font-medium underline">
          {l.label}
        </Link>
      ))}
    </p>
  );
}

export function AgentRunPage({ runId }: { runId: string }) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmEligibility, setConfirmEligibility] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getRun(runId).then(
      (found) => !cancelled && setRun(found),
      (e: unknown) =>
        !cancelled && setError(e instanceof ApiError ? e.message : "Could not load the run."),
    );
    return () => {
      cancelled = true;
    };
  }, [runId]);

  async function act(call: () => Promise<AgentRun>) {
    setBusy(true);
    setError(null);
    try {
      setRun(await call());
      setConfirmEligibility(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (!run) {
    return error ? (
      <p role="alert" className="text-sm text-red-700">
        {error}
      </p>
    ) : (
      <p role="status" className="text-sm text-zinc-500">
        Loading…
      </p>
    );
  }

  const open = ["ready", "waiting_for_human", "failed"].includes(run.status);
  const eligibility = run.pause?.kind === "eligibility_uncertain";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/agent" className="text-sm text-zinc-500 hover:underline">
            ← Agent runs
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Agent run
          </h1>
          <p className="text-sm text-zinc-500">Started {formatDateTime(run.created_at)}</p>
        </div>
        <span
          aria-label="Run status"
          className="rounded-full bg-zinc-900 px-3 py-1 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
        >
          {STATUS_LABELS[run.status]}
        </span>
      </div>

      <ol className="flex flex-wrap gap-2 text-xs" aria-label="Stages">
        {run.stages.map((s) => (
          <li
            key={s.stage}
            aria-current={s.state === "current" ? "step" : undefined}
            className={
              s.state === "done"
                ? "rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-900"
                : s.state === "current"
                  ? "rounded-full bg-zinc-900 px-2.5 py-1 text-white dark:bg-zinc-100 dark:text-zinc-900"
                  : "rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-500 dark:bg-zinc-800"
            }
          >
            {STAGE_LABELS[s.stage]}
          </li>
        ))}
      </ol>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      ) : null}

      {run.pause ? (
        <section
          aria-label="The agent needs you"
          className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950"
        >
          <p className="font-medium">
            {PAUSE_LABELS[run.pause.kind]} · {STAGE_LABELS[run.stage]}
          </p>
          <p className="mt-1">{run.pause.message}</p>
          {run.pause.items.length ? (
            <ul className="mt-2 list-disc pl-5">
              {run.pause.items.map((i) => (
                <li key={i}>{i}</li>
              ))}
            </ul>
          ) : null}
          <NextStep run={run} />
          {eligibility ? (
            <label className="mt-3 flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-1"
                checked={confirmEligibility}
                onChange={(e) => setConfirmEligibility(e.target.checked)}
              />
              <span>I meet these requirements (or want to apply anyway). Continue.</span>
            </label>
          ) : null}
        </section>
      ) : null}

      {run.status === "failed" && run.last_error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          The {STAGE_LABELS[run.stage]} stage failed: {run.last_error}. Continue to retry it.
        </p>
      ) : null}
      {run.status === "completed" ? (
        <p
          role="status"
          className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900"
        >
          Done: the application is submitted and being tracked.{" "}
          {run.application_id ? (
            <Link href={`/applications/${run.application_id}`} className="underline">
              Open the application
            </Link>
          ) : null}
        </p>
      ) : null}

      {open ? (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={busy || (eligibility && !confirmEligibility)}
            onClick={() =>
              void act(() => advanceRun(run.id, eligibility ? { confirm_eligibility: true } : {}))
            }
          >
            {busy
              ? "Working…"
              : run.status === "ready" && run.log.length <= 1
                ? "Start"
                : "Continue"}
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => void act(() => cancelRun(run.id))}>
            Cancel run
          </Button>
        </div>
      ) : null}

      <Card
        id="log"
        title="Execution log"
        description={`${run.steps} tool calls. Every action, in order; secrets are never logged.`}
      >
        <div className="relative overflow-x-auto">
          <table className="w-full text-left text-xs" aria-label="Execution log">
            <thead className="text-zinc-500">
              <tr>
                <th className="py-1 pr-3 font-medium">Time</th>
                <th className="py-1 pr-3 font-medium">Agent</th>
                <th className="py-1 pr-3 font-medium">Tool</th>
                <th className="py-1 pr-3 font-medium">Input</th>
                <th className="py-1 pr-3 font-medium">Output</th>
                <th className="py-1 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {run.log.map((e) => (
                <tr key={e.id} className="align-top">
                  <td className="py-1.5 pr-3 whitespace-nowrap text-zinc-500">
                    {formatDateTime(e.at)}
                  </td>
                  <td className="py-1.5 pr-3">{e.agent.replace(/_/g, " ")}</td>
                  <td className="py-1.5 pr-3 font-mono">{e.tool}</td>
                  <td className="py-1.5 pr-3">{e.input_summary}</td>
                  <td className="py-1.5 pr-3">
                    {e.output_summary}
                    {e.error ? <span className="block text-red-700">{e.error}</span> : null}
                  </td>
                  <td className="py-1.5">
                    <Badge
                      tone={
                        e.status === "failed" ? "ai" : e.status === "paused" ? "lock" : "neutral"
                      }
                    >
                      {e.status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
