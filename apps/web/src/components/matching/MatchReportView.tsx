"use client";

import { useState, type ReactNode } from "react";

import { Button, Card, EmptyState } from "@/components/ui";
import { IMPORTANCE_LABELS, REQUIREMENT_TYPE_LABELS } from "@/lib/jobs/labels";
import type { MatchReport, MatchStatus, RequirementMatch } from "@/lib/api/matching";

export const STATUS_LABELS: Record<MatchStatus, string> = {
  matched: "Matched",
  partial: "Partial",
  missing: "Missing",
  unknown: "Unknown",
};

const STATUS_STYLE: Record<MatchStatus, string> = {
  matched: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200",
  partial: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200",
  missing: "bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200",
  unknown: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

export const percent = (value: number | null) =>
  value === null ? "—" : `${Math.round(value * 100)}%`;

function StatusBadge({ status }: { status: MatchStatus }) {
  return (
    <span
      className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_STYLE[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

function RequirementItem({ item }: { item: RequirementMatch }) {
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start gap-2">
        <StatusBadge status={item.match_status} />
        <p className="min-w-0 flex-1 font-medium text-zinc-900 dark:text-zinc-100">
          {item.requirement}
        </p>
        <span className="text-xs text-zinc-500">
          {IMPORTANCE_LABELS[item.importance]} · {REQUIREMENT_TYPE_LABELS[item.requirement_type]}
        </span>
      </div>
      <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">{item.explanation}</p>
      {item.matching_candidate_evidence.length > 0 ? (
        <ul className="mt-2 space-y-1" aria-label="Matching evidence">
          {item.matching_candidate_evidence.map((e) => (
            <li
              key={e.evidence_id}
              className="rounded-md border-l-2 border-emerald-400 bg-zinc-50 px-3 py-1.5 text-sm dark:bg-zinc-900"
            >
              <span className="text-zinc-800 dark:text-zinc-200">{e.factual_content}</span>
              <span className="mt-0.5 block text-xs text-zinc-500">
                {[
                  e.source.record_label,
                  e.source.resume_file_name && `from ${e.source.resume_file_name}`,
                  e.similarity !== null && `similarity ${e.similarity.toFixed(2)}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function RequirementList({ items, empty }: { items: RequirementMatch[]; empty: string }) {
  if (items.length === 0) return <EmptyState>{empty}</EmptyState>;
  return (
    <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
      {items.map((item) => (
        <RequirementItem key={item.requirement_id} item={item} />
      ))}
    </ul>
  );
}

function Score({ label, value, hint }: { label: string; value: string; hint?: ReactNode }) {
  return (
    <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">{value}</p>
      {hint ? <p className="text-xs text-zinc-500">{hint}</p> : null}
    </div>
  );
}

const FILTERS: (MatchStatus | "all")[] = ["all", "matched", "partial", "missing", "unknown"];

export function MatchReportView({
  report,
  onRecompute,
  busy,
}: {
  report: MatchReport;
  onRecompute: () => void;
  busy: boolean;
}) {
  const [filter, setFilter] = useState<MatchStatus | "all">("all");
  const shown =
    filter === "all"
      ? report.requirements
      : report.requirements.filter((r) => r.match_status === filter);
  const computed = new Date(report.computed_at).toLocaleString("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  });

  return (
    <div className="space-y-6">
      {report.is_stale ? (
        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <span>Your profile has changed since this match was computed.</span>
          <Button size="sm" variant="primary" onClick={onRecompute} disabled={busy}>
            Update match
          </Button>
        </div>
      ) : null}

      <Card
        id="scores"
        title="Evidence coverage"
        description={report.disclaimer}
        actions={
          <Button size="sm" onClick={onRecompute} disabled={busy}>
            {busy ? "Matching…" : "Recompute"}
          </Button>
        }
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <Score
            label="Overall coverage"
            value={percent(report.scores.evidence_coverage)}
            hint="Required items weigh twice as much as preferred"
          />
          <Score label="Required requirements" value={percent(report.scores.required_coverage)} />
          <Score label="Preferred requirements" value={percent(report.scores.preferred_coverage)} />
        </div>
        <p className="mt-3 flex flex-wrap gap-2 text-sm" aria-label="Status counts">
          {(Object.keys(STATUS_LABELS) as MatchStatus[]).map((s) => (
            <span key={s} className="flex items-center gap-1">
              <StatusBadge status={s} /> {report.status_counts[s]}
            </span>
          ))}
        </p>
        <p className="mt-2 text-xs text-zinc-500">
          Computed {computed} ·{" "}
          {report.matcher === "rules"
            ? "rule-based judge"
            : `AI judge (${report.matcher?.replace(/^llm:/, "")})`}
          {report.informational_not_scored > 0
            ? ` · ${report.informational_not_scored} informational statement(s) not scored`
            : ""}
        </p>
        {report.warnings.length > 0 ? (
          <ul className="mt-3 list-disc pl-5 text-sm text-amber-800 dark:text-amber-300">
            {report.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}
      </Card>

      {report.potentially_disqualifying.length > 0 ? (
        <section
          aria-labelledby="disqualifying-heading"
          className="rounded-xl border border-red-300 bg-red-50/60 p-5 dark:border-red-900 dark:bg-red-950/20"
        >
          <h2
            id="disqualifying-heading"
            className="text-base font-semibold text-red-900 dark:text-red-200"
          >
            Potentially disqualifying
          </h2>
          <p className="mb-3 text-sm text-red-900/80 dark:text-red-200/80">
            Required eligibility conditions your profile doesn&apos;t show as met. Check these
            yourself before applying.
          </p>
          <RequirementList items={report.potentially_disqualifying} empty="" />
        </section>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card id="strongest" title="Strongest matches">
          <RequirementList
            items={report.strongest_matches}
            empty="No requirement is clearly matched yet."
          />
        </Card>
        <Card id="missing" title="Missing skills">
          <RequirementList
            items={report.missing_skills}
            empty="No required or preferred skills are missing."
          />
        </Card>
      </div>
      <Card
        id="partial"
        title="Partial matches"
        description="Related evidence that doesn't fully cover the requirement."
      >
        <RequirementList items={report.partial_matches} empty="No partial matches." />
      </Card>

      <Card id="all" title="All requirements">
        <div role="group" aria-label="Filter by status" className="mb-4 flex flex-wrap gap-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className="rounded-full border border-zinc-300 px-3 py-1 text-xs font-medium text-zinc-700 aria-pressed:border-zinc-900 aria-pressed:bg-zinc-900 aria-pressed:text-white dark:border-zinc-700 dark:text-zinc-300 dark:aria-pressed:bg-zinc-100 dark:aria-pressed:text-zinc-900"
            >
              {f === "all"
                ? `All (${report.requirements.length})`
                : `${STATUS_LABELS[f]} (${report.status_counts[f]})`}
            </button>
          ))}
        </div>
        <RequirementList items={shown} empty="No requirements with this status." />
      </Card>
    </div>
  );
}
