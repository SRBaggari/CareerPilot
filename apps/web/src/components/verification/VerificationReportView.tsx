"use client";

import { useState } from "react";

import { EmptyState } from "@/components/ui";
import {
  STATUS_HELP,
  STATUS_LABELS,
  STATUS_ORDER,
  type ClaimResult,
  type VerificationReport,
  type VerificationStatus,
} from "@/lib/api/verification";

const TONES: Record<VerificationStatus, string> = {
  supported: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200",
  partially_supported: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200",
  unsupported: "bg-zinc-200 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200",
  contradicted: "bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200",
};

const TYPE_LABELS: Record<string, string> = {
  contact: "Contact details",
  employment: "Job",
  project_entry: "Project",
  education: "Education",
  certification: "Certification",
  achievement: "Achievement",
  coursework: "Coursework",
  summary: "Summary",
  skill: "Skill",
  experience: "Job bullet",
  project: "Project bullet",
  statement: "Statement",
};

export function StatusBadge({ status }: { status: VerificationStatus }) {
  return (
    <span
      title={STATUS_HELP[status]}
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${TONES[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

type Evidence = Record<string, { content: string; record_label: string | null }>;

function ClaimRow({ result, evidence }: { result: ClaimResult; evidence: Evidence }) {
  const sources = result.evidence_ids.map((id) => evidence[id]).filter(Boolean);
  return (
    <li className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={result.verification_status} />
        <span className="text-xs text-zinc-500">
          {TYPE_LABELS[result.claim_type] ?? result.claim_type} · confidence{" "}
          {Math.round(result.confidence * 100)}%{result.method === "llm" ? " · AI reviewer" : ""}
          {result.evidence_source === "profile" ? " · checked against your profile" : ""}
        </span>
      </div>
      <p className="mt-1 text-sm text-zinc-900 dark:text-zinc-100">{result.claim_text}</p>
      <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{result.reason}</p>
      {sources.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {sources.map((source) => (
            <li
              key={source.content}
              className="border-l-2 border-sky-300 pl-2 text-xs text-sky-900 dark:border-sky-700 dark:text-sky-300"
            >
              Evidence{source.record_label ? ` (${source.record_label})` : ""}: “{source.content}”
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/**
 * A verification report: the outcome, a count per status, and every claim with its status,
 * confidence, reason and evidence. Problems are listed first.
 */
export function VerificationReportView({
  report,
  evidence = {},
}: {
  report: VerificationReport;
  evidence?: Evidence;
}) {
  const [filter, setFilter] = useState<VerificationStatus | "all">("all");
  const rank = (s: VerificationStatus) => STATUS_ORDER.indexOf(s);
  const shown = [...report.claims]
    .filter((c) => filter === "all" || c.verification_status === filter)
    .sort((a, b) => rank(a.verification_status) - rank(b.verification_status));
  const approved = report.outcome === "approved";
  return (
    <div className="space-y-3">
      <div
        role="status"
        aria-label="Verification outcome"
        className={`rounded-md px-3 py-2 text-sm font-medium ${
          approved
            ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
            : "bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-200"
        }`}
      >
        {approved
          ? "Approved: every claim is supported by your evidence."
          : "Rejected: some claims aren't supported by your evidence."}
      </div>
      <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
        <FilterButton active={filter === "all"} onClick={() => setFilter("all")}>
          All {report.claims.length}
        </FilterButton>
        {STATUS_ORDER.map((status) => (
          <FilterButton key={status} active={filter === status} onClick={() => setFilter(status)}>
            {STATUS_LABELS[status]} {report.counts[status]}
          </FilterButton>
        ))}
      </div>
      {report.warnings.length > 0 ? (
        <ul className="list-disc pl-5 text-sm text-amber-800 dark:text-amber-300">
          {report.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      {shown.length === 0 ? (
        <EmptyState>No claims with this status.</EmptyState>
      ) : (
        <ul className="space-y-2" aria-label="Verified claims">
          {shown.map((result) => (
            <ClaimRow
              key={`${result.section}-${result.position}-${result.claim_type}`}
              result={result}
              evidence={evidence}
            />
          ))}
        </ul>
      )}
      <p className="text-xs text-zinc-500">
        Checked by {report.verifier === "rules" ? "rule checks" : report.verifier}
        {report.created_at
          ? ` on ${new Date(report.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}`
          : ""}
        .
      </p>
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className="rounded-full border border-zinc-300 px-2.5 py-0.5 text-xs text-zinc-700 aria-pressed:border-zinc-900 aria-pressed:bg-zinc-900 aria-pressed:text-white dark:border-zinc-700 dark:text-zinc-300 dark:aria-pressed:bg-zinc-100 dark:aria-pressed:text-zinc-900"
    >
      {children}
    </button>
  );
}
