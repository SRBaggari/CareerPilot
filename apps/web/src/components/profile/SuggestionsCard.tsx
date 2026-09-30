"use client";

import { useEffect, useState } from "react";

import { Badge, Button } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  acceptSuggestion,
  listSuggestions,
  rejectSuggestion,
  type Suggestion,
} from "@/lib/api/profile";

const SECTION_LABELS: Record<string, string> = {
  personal_info: "Personal information",
  education: "Education",
  work_experience: "Work experience",
  project: "Project",
  certification: "Certification",
  achievement: "Achievement",
  coursework: "Coursework",
  skill: "Skill",
  evidence: "Highlight",
};

const formatValue = (value: unknown) =>
  Array.isArray(value) ? value.join(", ") : value === null ? "—" : String(value);

/**
 * AI-GENERATED CONTENT. Kept visually and structurally separate from the profile:
 * nothing here is part of the master profile until the candidate accepts it.
 */
export function SuggestionsCard({
  count,
  onChanged,
}: {
  count: number;
  onChanged: () => Promise<void>;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Refetch whenever the pending count changes (it drops after every accept/reject).
  useEffect(() => {
    if (count === 0) return;
    let cancelled = false;
    listSuggestions().then(
      (items) => !cancelled && setSuggestions(items),
      (e: unknown) =>
        !cancelled && setError(e instanceof ApiError ? e.message : "Could not load suggestions."),
    );
    return () => {
      cancelled = true;
    };
  }, [count]);

  if (count === 0) return null;

  async function decide(id: string, decision: "accept" | "reject") {
    setBusyId(id);
    setError(null);
    try {
      await (decision === "accept" ? acceptSuggestion(id) : rejectSuggestion(id));
      await onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not apply the suggestion.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section
      aria-labelledby="suggestions-heading"
      className="rounded-xl border-2 border-dashed border-amber-300 bg-amber-50/60 p-5 dark:border-amber-800 dark:bg-amber-950/20"
    >
      <div className="flex items-center gap-2">
        <h2
          id="suggestions-heading"
          className="text-base font-semibold text-amber-950 dark:text-amber-100"
        >
          AI suggestions awaiting your review
        </h2>
        <Badge tone="ai">AI-generated</Badge>
      </div>
      <p className="mt-1 text-sm text-amber-900/80 dark:text-amber-200/80">
        These were produced automatically and are <strong>not part of your profile</strong>. Nothing
        changes unless you accept it. Check every detail — only accept what is true.
      </p>
      {error ? (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      ) : null}
      <ul className="mt-4 space-y-3">
        {suggestions.map((s) => (
          <li
            key={s.id}
            className="rounded-lg border border-amber-200 bg-white p-4 dark:border-amber-900 dark:bg-zinc-950"
          >
            <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
              {s.action === "create" ? "Add" : "Update"} {SECTION_LABELS[s.section] ?? s.section}
              <span className="ml-2 text-xs font-normal text-zinc-500">
                {s.source === "resume_extraction" ? "from your uploaded resume" : "AI-generated"}
              </span>
            </p>
            {s.rationale ? <p className="mt-0.5 text-xs text-zinc-500">{s.rationale}</p> : null}
            <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
              {Object.entries(s.proposed_data).map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="text-zinc-500">{key.replaceAll("_", " ")}</dt>
                  <dd className="text-zinc-800 dark:text-zinc-200">{formatValue(value)}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-3 flex gap-2">
              <Button
                size="sm"
                variant="primary"
                disabled={busyId === s.id}
                onClick={() => void decide(s.id, "accept")}
              >
                Accept
              </Button>
              <Button
                size="sm"
                disabled={busyId === s.id}
                onClick={() => void decide(s.id, "reject")}
              >
                Reject
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
