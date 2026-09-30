"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Button, Card } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { computeMatch, getMatch, type MatchReport } from "@/lib/api/matching";

import { MatchReportView } from "./MatchReportView";

type State =
  | { status: "loading" }
  | { status: "empty" }
  | { status: "ready"; report: MatchReport }
  | { status: "error"; message: string };

export function JobMatchPage({ jobId }: { jobId: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMatch(jobId).then(
      (report) =>
        !cancelled && setState(report ? { status: "ready", report } : { status: "empty" }),
      (e: unknown) =>
        !cancelled &&
        setState({
          status: "error",
          message: e instanceof ApiError ? e.message : "Could not load the match.",
        }),
    );
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  async function compute() {
    setBusy(true);
    setError(null);
    try {
      setState({ status: "ready", report: await computeMatch(jobId) });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Matching failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const title =
    state.status === "ready" ? `${state.report.job_title} · ${state.report.company_name}` : null;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href={`/jobs/${jobId}`} className="text-sm text-zinc-500 hover:underline">
            ← Job analysis
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Job match
          </h1>
          {title ? <p className="text-zinc-600 dark:text-zinc-400">{title}</p> : null}
        </div>
        {state.status === "ready" ? (
          <Link
            href={`/jobs/${jobId}/resume`}
            className="inline-flex items-center rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
          >
            Tailor my resume
          </Link>
        ) : null}
      </div>
      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}{" "}
          {error.toLowerCase().includes("profile") ? (
            <Link href="/profile" className="underline">
              Go to your profile
            </Link>
          ) : null}
        </p>
      ) : null}
      {state.status === "loading" ? (
        <p role="status" className="text-sm text-zinc-500">
          Loading…
        </p>
      ) : null}
      {state.status === "error" ? (
        <p role="alert" className="text-sm text-red-700">
          {state.message}
        </p>
      ) : null}
      {state.status === "empty" ? (
        <Card
          title="Compare this job with your profile"
          description="CareerPilot checks each stated requirement against your verified evidence and explains every result."
        >
          <Button variant="primary" onClick={() => void compute()} disabled={busy}>
            {busy ? "Matching…" : "Run match"}
          </Button>
        </Card>
      ) : null}
      {state.status === "ready" ? (
        <MatchReportView report={state.report} busy={busy} onRecompute={() => void compute()} />
      ) : null}
    </div>
  );
}
