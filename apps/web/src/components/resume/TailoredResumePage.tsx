"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  downloadUrl,
  generateTailoredResume,
  getLatestTailoredResume,
  type TailoredResume,
} from "@/lib/api/tailoredResumes";

import { ResumeEditor } from "./ResumeEditor";
import { ResumePreview } from "./ResumePreview";

type State =
  | { status: "loading" }
  | { status: "empty" }
  | { status: "ready"; resume: TailoredResume }
  | { status: "error"; message: string };

const linkButton =
  "inline-flex items-center rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-800 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800";

/** What verification did: how many claims passed, and every rewrite or rejection with why. */
export function VerificationPanel({ resume }: { resume: TailoredResume }) {
  const { verified_claims, rewritten, rejected, audit } = resume.verification;
  return (
    <Card
      id="verification"
      title="Claim verification"
      description="Every statement was checked against the evidence it cites. Unsupported statements were rewritten to your evidence or removed."
    >
      <p className="flex flex-wrap gap-3 text-sm" aria-label="Verification summary">
        <span>
          <strong>{verified_claims}</strong> verified
        </span>
        <span>
          <strong>{rewritten}</strong> rewritten
        </span>
        <span>
          <strong>{rejected}</strong> rejected
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
                <Badge tone={item.outcome === "rewritten" ? "lock" : "ai"}>
                  {item.outcome === "rewritten" ? "Rewritten" : "Rejected"}
                </Badge>
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
        <EmptyState>Every generated statement was supported as written.</EmptyState>
      )}
      {resume.notes.length > 0 ? (
        <ul className="mt-4 list-disc pl-5 text-sm text-amber-800 dark:text-amber-300">
          {resume.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

export function TailoredResumePage({ jobId }: { jobId: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLatestTailoredResume(jobId).then(
      (resume) =>
        !cancelled && setState(resume ? { status: "ready", resume } : { status: "empty" }),
      (e: unknown) =>
        !cancelled &&
        setState({
          status: "error",
          message: e instanceof ApiError ? e.message : "Could not load the resume.",
        }),
    );
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  async function generate() {
    if (
      state.status === "ready" &&
      !window.confirm("Regenerate? This replaces the current version, including your edits.")
    )
      return;
    setBusy(true);
    setError(null);
    try {
      setState({ status: "ready", resume: await generateTailoredResume(jobId) });
      setEditing(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Tailoring failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const resume = state.status === "ready" ? state.resume : null;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href={`/jobs/${jobId}/match`} className="text-sm text-zinc-500 hover:underline">
            ← Job match
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Tailored resume
          </h1>
          {resume ? (
            <p className="text-zinc-600 dark:text-zinc-400">
              {resume.job_title} · {resume.company_name} · version {resume.version} ·{" "}
              {resume.generator === "rules"
                ? "rule-based"
                : `AI wording (${resume.generator?.replace(/^llm:/, "")})`}
            </p>
          ) : null}
        </div>
        {resume && !editing ? (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setEditing(true)} disabled={busy}>
              Edit
            </Button>
            <Button onClick={() => void generate()} disabled={busy}>
              {busy ? "Tailoring…" : "Regenerate"}
            </Button>
            <a href={downloadUrl(resume.id, "pdf")} className={linkButton} download>
              Download PDF
            </a>
            <a href={downloadUrl(resume.id, "docx")} className={linkButton} download>
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
          title="Tailor your resume to this job"
          description="CareerPilot selects and orders your most relevant experience, projects, skills, and coursework, and words them for this job. Nothing is invented: every statement is checked against your verified evidence."
        >
          <Button variant="primary" onClick={() => void generate()} disabled={busy}>
            {busy ? "Tailoring…" : "Generate tailored resume"}
          </Button>
        </Card>
      ) : null}

      {resume && editing ? (
        <ResumeEditor
          resume={resume}
          onCancel={() => setEditing(false)}
          onSaved={(saved) => {
            setState({ status: "ready", resume: saved });
            setEditing(false);
          }}
        />
      ) : null}
      {resume && !editing ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
              <input
                type="checkbox"
                checked={showSources}
                onChange={(e) => setShowSources(e.target.checked)}
              />
              Show the evidence behind each statement
            </label>
            <ResumePreview
              content={resume.content}
              evidence={resume.evidence}
              showSources={showSources}
            />
          </div>
          <VerificationPanel resume={resume} />
        </div>
      ) : null}
    </div>
  );
}
