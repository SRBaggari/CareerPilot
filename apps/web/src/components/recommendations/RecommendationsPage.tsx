"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Button, EmptyState } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  listRecommendations,
  refreshRecommendations,
  type RecommendationList,
  type RecommendationView,
} from "@/lib/api/recommendations";

import { RecommendationCard } from "./RecommendationCard";

const VIEWS: { view: RecommendationView; label: string }[] = [
  { view: "recommended", label: "Recommended" },
  { view: "saved", label: "Saved" },
  { view: "ignored", label: "Ignored" },
  { view: "filtered_out", label: "Filtered out" },
];

export function RecommendationsPage() {
  const [view, setView] = useState<RecommendationView>("recommended");
  const [list, setList] = useState<RecommendationList | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRecommendations(view).then(
      (found) => !cancelled && setList(found),
      (e: unknown) =>
        !cancelled &&
        setError(e instanceof ApiError ? e.message : "Could not load recommendations."),
    );
    return () => {
      cancelled = true;
    };
  }, [view]);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      const fresh = await refreshRecommendations();
      setView("recommended");
      setList(fresh);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Refreshing failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Recommended jobs
          </h1>
          <p className="max-w-2xl text-zinc-600 dark:text-zinc-400">
            Jobs from your sources, matched against your verified evidence and filtered for
            eligibility and your preferences. Each one explains why it&apos;s recommended.
          </p>
          {list?.refreshed_at ? (
            <p className="text-xs text-zinc-500">
              Last refreshed{" "}
              {new Date(list.refreshed_at).toLocaleString("en-GB", {
                dateStyle: "medium",
                timeStyle: "short",
              })}
            </p>
          ) : null}
        </div>
        <Button variant="primary" onClick={() => void refresh()} disabled={busy}>
          {busy ? "Finding jobs…" : "Refresh recommendations"}
        </Button>
      </div>

      <nav aria-label="Recommendation views" className="flex flex-wrap gap-1">
        {VIEWS.map(({ view: v, label }) => (
          <button
            key={v}
            type="button"
            aria-pressed={view === v}
            onClick={() => setView(v)}
            className="rounded-full border border-zinc-300 px-3 py-1 text-sm aria-pressed:border-zinc-900 aria-pressed:bg-zinc-900 aria-pressed:text-white dark:border-zinc-700 dark:aria-pressed:bg-zinc-100 dark:aria-pressed:text-zinc-900"
          >
            {label} {list ? list.counts[v] : ""}
          </button>
        ))}
      </nav>

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
      {list?.errors.length ? (
        <ul
          role="status"
          className="list-disc rounded-lg border border-amber-300 bg-amber-50 p-3 pl-8 text-sm text-amber-900"
        >
          {list.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}

      {list === null && !error ? (
        <p role="status" className="text-sm text-zinc-500">
          Loading…
        </p>
      ) : null}
      {list && list.recommendations.length === 0 ? (
        <EmptyState>
          {view === "recommended"
            ? list.refreshed_at
              ? "No jobs are recommended yet. Add verified evidence to your profile, adjust your preferences, or check the filtered-out list."
              : "No recommendations yet. Refresh to find jobs that match your verified evidence."
            : "Nothing here."}
        </EmptyState>
      ) : null}
      <div className="space-y-4">
        {list?.recommendations.map((rec) => (
          <RecommendationCard
            key={rec.id}
            rec={rec}
            onChange={(updated) =>
              setList(
                (current) =>
                  current && {
                    ...current,
                    recommendations: current.recommendations.map((r) =>
                      r.id === updated.id ? updated : r,
                    ),
                  },
              )
            }
          />
        ))}
      </div>
      <p className="text-xs text-zinc-500">
        Recommendations show how your verified evidence covers each job&apos;s stated requirements.
        They are not a prediction of being hired.
      </p>
    </div>
  );
}
