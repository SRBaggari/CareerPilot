"use client";

import { useState } from "react";

import { Badge, Button, EmptyState, inputClass } from "@/components/ui";
import {
  approveAnswer,
  deleteAnswer,
  QUESTION_TYPE_LABELS,
  regenerateAnswer,
  saveAnswer,
  type ApplicationAnswer,
} from "@/lib/api/applicationAnswers";
import { ApiError } from "@/lib/api/client";

import { VerificationReportView } from "../verification/VerificationReportView";

const STATUS: Record<string, { label: string; tone: string }> = {
  approved: {
    label: "Approved",
    tone: "bg-emerald-600 text-white dark:bg-emerald-500 dark:text-emerald-950",
  },
  verified: {
    label: "Verified",
    tone: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200",
  },
  verification_failed: {
    label: "Failed verification",
    tone: "bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200",
  },
  draft: {
    label: "No answer yet",
    tone: "bg-zinc-200 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200",
  },
};

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="text-xs font-semibold tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
      {children}
    </h4>
  );
}

/** One application question: the question, the answer, the evidence it uses, and its status. */
export function AnswerCard({
  answer,
  onChange,
  onDeleted,
}: {
  answer: ApplicationAnswer;
  onChange: (answer: ApplicationAnswer) => void;
  onDeleted: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(answer.text);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showReport, setShowReport] = useState(false);
  const status = STATUS[answer.status] ?? STATUS.draft;
  const headingId = `answer-${answer.id}`;

  async function run(action: string, call: () => Promise<ApplicationAnswer | void>) {
    setBusy(action);
    setError(null);
    try {
      const result = await call();
      if (result) {
        onChange(result);
        setDraft(result.text);
        setEditing(false);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section
      aria-labelledby={headingId}
      className="space-y-4 rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <Heading>Question</Heading>
          <h3 id={headingId} className="font-medium text-zinc-900 dark:text-zinc-50">
            {answer.question}
          </h3>
          <p className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
            <Badge>{QUESTION_TYPE_LABELS[answer.question_type] ?? answer.question_type}</Badge>
            {answer.understanding}
            {answer.max_words ? ` · limit ${answer.max_words} words` : ""}
          </p>
        </div>
        <div className="space-y-1 text-right">
          <Heading>Verification status</Heading>
          <span
            aria-label="Verification status"
            className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${status.tone}`}
          >
            {status.label}
          </span>
        </div>
      </div>

      <div className="space-y-1">
        <Heading>Generated answer</Heading>
        {editing ? (
          <div className="space-y-2">
            <label htmlFor={`${headingId}-edit`} className="sr-only">
              Answer to: {answer.question}
            </label>
            <textarea
              id={`${headingId}-edit`}
              rows={5}
              className={inputClass}
              value={draft}
              aria-invalid={error ? true : undefined}
              onChange={(e) => setDraft(e.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="primary"
                disabled={busy !== null || !draft.trim()}
                onClick={() => void run("save", () => saveAnswer(answer.id, draft))}
              >
                {busy === "save" ? "Verifying…" : "Save"}
              </Button>
              <Button
                size="sm"
                disabled={busy !== null}
                onClick={() => {
                  setEditing(false);
                  setDraft(answer.text);
                  setError(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : answer.text ? (
          <p className="text-sm leading-6 text-zinc-800 dark:text-zinc-200">{answer.text}</p>
        ) : (
          <EmptyState>
            No answer: your verified evidence doesn&apos;t cover this question.
          </EmptyState>
        )}
        {answer.text && !editing ? (
          <p className="text-xs text-zinc-500">
            {answer.word_count} words ·{" "}
            {answer.generator === "rules"
              ? "rule-based"
              : answer.generator
                ? `AI wording (${answer.generator.replace(/^llm:/, "")})`
                : "your edit"}
          </p>
        ) : null}
      </div>

      {error ? (
        <p role="alert" className="rounded-md bg-red-50 p-2 text-sm text-red-800">
          {error}
        </p>
      ) : null}
      {answer.notes.length > 0 ? (
        <ul className="list-disc pl-5 text-sm text-amber-800 dark:text-amber-300">
          {answer.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      <div className="space-y-1">
        <Heading>Evidence used</Heading>
        {answer.evidence_used.length > 0 ? (
          <ul className="space-y-1" aria-label="Evidence used">
            {answer.evidence_used.map((e) => (
              <li
                key={e.evidence_id}
                className="border-l-2 border-sky-300 pl-2 text-sm text-sky-900 dark:border-sky-700 dark:text-sky-300"
              >
                “{e.content}”
                {e.record_label ? (
                  <span className="text-xs text-zinc-500"> · {e.record_label}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>No evidence cited.</EmptyState>
        )}
      </div>

      {answer.changes.audit.length > 0 ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">
            {answer.changes.regenerated} regenerated, {answer.changes.removed} removed during
            generation
          </summary>
          <ul className="mt-2 space-y-2">
            {answer.changes.audit.map((item, i) => (
              <li key={i} className="rounded-md border border-zinc-200 p-2 dark:border-zinc-800">
                <p className="text-zinc-500 line-through">{item.original_text}</p>
                {item.final_text ? <p>→ {item.final_text}</p> : null}
                <p className="text-xs text-zinc-600 dark:text-zinc-400">{item.reason}</p>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {answer.report && answer.report.claims.length > 0 ? (
        <div>
          <Button size="sm" variant="ghost" onClick={() => setShowReport(!showReport)}>
            {showReport ? "Hide verification report" : "Show verification report"}
          </Button>
          {showReport ? (
            <div className="mt-2">
              <VerificationReportView
                report={answer.report}
                evidence={Object.fromEntries(answer.evidence_used.map((e) => [e.evidence_id, e]))}
              />
            </div>
          ) : null}
        </div>
      ) : null}

      {!editing ? (
        <div className="flex flex-wrap gap-2 border-t border-zinc-100 pt-3 dark:border-zinc-900">
          <Button size="sm" disabled={busy !== null} onClick={() => setEditing(true)}>
            Edit
          </Button>
          <Button
            size="sm"
            disabled={busy !== null}
            onClick={() => {
              if (
                answer.status !== "approved" ||
                window.confirm("Regenerate? This replaces your approved answer.")
              )
                void run("regenerate", () => regenerateAnswer(answer.id));
            }}
          >
            {busy === "regenerate" ? "Writing…" : "Regenerate"}
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={busy !== null || answer.status === "approved" || !answer.text}
            onClick={() => void run("approve", () => approveAnswer(answer.id))}
          >
            {answer.status === "approved"
              ? "Approved"
              : busy === "approve"
                ? "Checking…"
                : "Approve"}
          </Button>
          <Button
            size="sm"
            variant="danger"
            disabled={busy !== null}
            onClick={() => {
              if (window.confirm("Delete this question and its answer?"))
                void run("delete", async () => {
                  await deleteAnswer(answer.id);
                  onDeleted(answer.id);
                });
            }}
          >
            Delete
          </Button>
        </div>
      ) : null}
    </section>
  );
}
