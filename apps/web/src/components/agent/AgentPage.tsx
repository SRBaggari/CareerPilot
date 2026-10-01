"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, Card, EmptyState, Field, inputClass } from "@/components/ui";
import { listRuns, STAGE_LABELS, startRun, STATUS_LABELS, type AgentRun } from "@/lib/api/agent";
import { formatDateTime } from "@/lib/api/applications";
import { ApiError } from "@/lib/api/client";
import { listJobs, type JobSummary } from "@/lib/api/jobs";

/** Start an agent run for one of your jobs, and see earlier runs. */
export function AgentPage({ initialJobId = "" }: { initialJobId?: string }) {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [jobId, setJobId] = useState(initialJobId);
  const [letter, setLetter] = useState(true);
  const [questions, setQuestions] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listJobs(), listRuns()]).then(
      ([j, r]) => {
        if (cancelled) return;
        setJobs(j);
        setRuns(r);
      },
      (e: unknown) =>
        !cancelled && setError(e instanceof ApiError ? e.message : "Could not load your jobs."),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const run = await startRun({
        job_id: jobId,
        include_cover_letter: letter,
        questions: questions
          .split("\n")
          .map((q) => q.trim())
          .filter(Boolean),
      });
      router.push(`/agent/${run.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not start the agent.");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Application agent
        </h1>
        <p className="max-w-3xl text-sm text-zinc-600 dark:text-zinc-400">
          The agent takes a job through discover, analyze, match, prepare, verify, review, approve,
          submit and track, one validated step at a time. It stops whenever it needs you: missing
          information, uncertain eligibility, failed verification, ambiguous questions, and every
          approval. It never approves or submits for you, and every action is logged.
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
      <Card id="start" title="Start">
        {jobs === null ? (
          <p role="status" className="text-sm text-zinc-500">
            Loading…
          </p>
        ) : jobs.length === 0 ? (
          <EmptyState>
            Add a job first:{" "}
            <Link href="/jobs" className="underline">
              Jobs
            </Link>{" "}
            or{" "}
            <Link href="/discover" className="underline">
              Discover
            </Link>
            .
          </EmptyState>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              void start();
            }}
          >
            <Field id="agent-job" label="Job" required>
              <select
                id="agent-job"
                className={inputClass}
                value={jobId}
                onChange={(e) => setJobId(e.target.value)}
              >
                <option value="">Choose a job…</option>
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.title} · {j.company_name}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={letter}
                onChange={(e) => setLetter(e.target.checked)}
              />
              Write a cover letter
            </label>
            <Field
              id="agent-questions"
              label="Application questions (optional, one per line)"
              hint="Questions the application asks. Answers come only from your verified evidence."
            >
              <textarea
                id="agent-questions"
                rows={3}
                className={inputClass}
                value={questions}
                onChange={(e) => setQuestions(e.target.value)}
              />
            </Field>
            <Button type="submit" variant="primary" disabled={busy || !jobId}>
              Create the run
            </Button>
          </form>
        )}
      </Card>
      <Card id="runs" title="Runs">
        {runs.length === 0 ? (
          <EmptyState>No runs yet.</EmptyState>
        ) : (
          <ul
            className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800"
            aria-label="Agent runs"
          >
            {runs.map((r) => (
              <li key={r.id} className="flex justify-between gap-4 py-2">
                <Link href={`/agent/${r.id}`} className="underline">
                  {formatDateTime(r.created_at)}
                </Link>
                <span>
                  {STAGE_LABELS[r.stage]} · {STATUS_LABELS[r.status]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
