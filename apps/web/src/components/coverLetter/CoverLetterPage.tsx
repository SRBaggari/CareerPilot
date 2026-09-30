"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  coverLetterDownloadUrl,
  generateCoverLetter,
  getLatestCoverLetter,
  reverifyCoverLetter,
  type CoverLetter,
} from "@/lib/api/coverLetters";

import { StatusBadge, VerificationReportView } from "../verification/VerificationReportView";
import { CoverLetterEditor } from "./CoverLetterEditor";

type State =
  | { status: "loading" }
  | { status: "empty" }
  | { status: "ready"; letter: CoverLetter }
  | { status: "error"; message: string };

const linkButton =
  "inline-flex items-center rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-800 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800";

/** The letter as it will be downloaded; optionally with the evidence behind each sentence. */
export function CoverLetterPreview({
  letter,
  showSources,
}: {
  letter: CoverLetter;
  showSources: boolean;
}) {
  const c = letter.content;
  const contact = [c.signature.contact_email, c.signature.phone, c.signature.location]
    .filter(Boolean)
    .join(" | ");
  return (
    <article
      aria-label="Cover letter preview"
      className="space-y-4 rounded-xl border border-zinc-200 bg-white p-8 text-sm leading-6 text-zinc-800 shadow-sm dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-200"
    >
      <div>
        <p className="text-lg font-bold text-zinc-900 dark:text-zinc-50">{c.signature.full_name}</p>
        {contact ? <p className="text-zinc-500">{contact}</p> : null}
      </div>
      <p className="font-semibold">
        Re: {c.job_title}, {c.company_name}
      </p>
      <p>{c.greeting}</p>
      {c.paragraphs.map((paragraph, index) => (
        <p key={index}>
          {paragraph.sentences.map((sentence, n) => (
            <span key={sentence.claim_id ?? n}>
              {n > 0 ? " " : ""}
              {sentence.text}
              {showSources
                ? sentence.evidence_ids
                    .map((id) => letter.evidence[id])
                    .filter(Boolean)
                    .map((source) => (
                      <span
                        key={source.content}
                        className="my-1 block border-l-2 border-sky-300 pl-2 text-xs text-sky-900 dark:border-sky-700 dark:text-sky-300"
                      >
                        Evidence{source.record_label ? ` (${source.record_label})` : ""}: “
                        {source.content}”
                      </span>
                    ))
                : null}
            </span>
          ))}
        </p>
      ))}
      <p>
        {c.closing}
        <br />
        {c.signature.full_name}
      </p>
    </article>
  );
}

function GenerationChanges({ letter }: { letter: CoverLetter }) {
  const { kept, regenerated, removed, audit } = letter.changes;
  return (
    <Card
      id="letter-changes"
      title="Changes made during generation"
      description="Sentences the verification engine didn't approve were regenerated from your evidence or removed."
    >
      <p className="flex flex-wrap gap-3 text-sm" aria-label="Generation summary">
        <span>
          <strong>{kept}</strong> kept
        </span>
        <span>
          <strong>{regenerated}</strong> regenerated
        </span>
        <span>
          <strong>{removed}</strong> removed
        </span>
      </p>
      {audit.length > 0 ? (
        <ul className="mt-3 space-y-3 text-sm">
          {audit.map((item, i) => (
            <li
              key={`${item.section}-${i}`}
              className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={item.outcome === "regenerated" ? "lock" : "ai"}>
                  {item.outcome === "regenerated" ? "Regenerated" : "Removed"}
                </Badge>
                <StatusBadge status={item.verdict} />
                <span className="text-xs text-zinc-500">{item.section}</span>
              </div>
              <p className="mt-1 text-zinc-500 line-through">{item.original_text}</p>
              {item.final_text ? (
                <p className="text-zinc-800 dark:text-zinc-200">→ {item.final_text}</p>
              ) : null}
              <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{item.reason}</p>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState>Every generated sentence was supported as written.</EmptyState>
      )}
      {letter.notes.length > 0 ? (
        <ul className="mt-4 list-disc pl-5 text-sm text-amber-800 dark:text-amber-300">
          {letter.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

export function CoverLetterPage({ jobId }: { jobId: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [editing, setEditing] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLatestCoverLetter(jobId).then(
      (letter) =>
        !cancelled && setState(letter ? { status: "ready", letter } : { status: "empty" }),
      (e: unknown) =>
        !cancelled &&
        setState({
          status: "error",
          message: e instanceof ApiError ? e.message : "Could not load the cover letter.",
        }),
    );
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  async function generate() {
    if (
      state.status === "ready" &&
      !window.confirm("Regenerate? This replaces the current letter, including your edits.")
    )
      return;
    setBusy(true);
    setError(null);
    try {
      setState({ status: "ready", letter: await generateCoverLetter(jobId) });
      setEditing(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Writing the letter failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function reverify(id: string) {
    setVerifying(true);
    setError(null);
    try {
      setState({ status: "ready", letter: await reverifyCoverLetter(id) });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Verification failed. Please try again.");
    } finally {
      setVerifying(false);
    }
  }

  const letter = state.status === "ready" ? state.letter : null;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href={`/jobs/${jobId}/match`} className="text-sm text-zinc-500 hover:underline">
            ← Job match
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Cover letter
          </h1>
          {letter ? (
            <p className="text-zinc-600 dark:text-zinc-400">
              {letter.job_title} · {letter.company_name} · version {letter.version} ·{" "}
              {letter.word_count} words ·{" "}
              {letter.generator === "rules"
                ? "rule-based"
                : `AI wording (${letter.generator?.replace(/^llm:/, "")})`}
            </p>
          ) : null}
        </div>
        {letter && !editing ? (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setEditing(true)} disabled={busy}>
              Edit
            </Button>
            <Button onClick={() => void generate()} disabled={busy}>
              {busy ? "Writing…" : "Regenerate"}
            </Button>
            <a href={coverLetterDownloadUrl(letter.id, "pdf")} className={linkButton} download>
              Download PDF
            </a>
            <a href={coverLetterDownloadUrl(letter.id, "docx")} className={linkButton} download>
              Download DOCX
            </a>
          </div>
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
      {letter?.status === "verification_failed" && !editing ? (
        <p
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
        >
          This letter failed verification: some sentences aren&apos;t supported by your current
          evidence or profile. Review the report, then edit or regenerate it before you send it.
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
          title="Write a cover letter for this job"
          description="A concise letter built from your verified evidence. Every sentence about you is checked by the verification engine; anything it can't support is regenerated from your evidence or removed."
        >
          <Button variant="primary" onClick={() => void generate()} disabled={busy}>
            {busy ? "Writing…" : "Generate cover letter"}
          </Button>
        </Card>
      ) : null}

      {letter && editing ? (
        <CoverLetterEditor
          letter={letter}
          onCancel={() => setEditing(false)}
          onSaved={(saved) => {
            setState({ status: "ready", letter: saved });
            setEditing(false);
          }}
        />
      ) : null}
      {letter && !editing ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
              <input
                type="checkbox"
                checked={showSources}
                onChange={(e) => setShowSources(e.target.checked)}
              />
              Show the evidence behind each sentence
            </label>
            <CoverLetterPreview letter={letter} showSources={showSources} />
          </div>
          <div className="space-y-6">
            <Card
              id="letter-verification"
              title="Verification report"
              description="Every sentence checked against your verified evidence and profile. Greetings and statements of interest make no factual claims; everything else must be supported."
              actions={
                <Button size="sm" onClick={() => void reverify(letter.id)} disabled={verifying}>
                  {verifying ? "Verifying…" : "Re-verify"}
                </Button>
              }
            >
              {letter.report ? (
                <VerificationReportView report={letter.report} evidence={letter.evidence} />
              ) : (
                <EmptyState>This letter hasn&apos;t been verified yet.</EmptyState>
              )}
            </Card>
            <GenerationChanges letter={letter} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
