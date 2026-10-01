"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ResumePreview } from "@/components/resume/ResumePreview";
import { Badge, Button, Card, EmptyState, Field, inputClass } from "@/components/ui";
import { formatDateTime } from "@/lib/api/applications";
import { ApiError } from "@/lib/api/client";
import { paragraphText } from "@/lib/api/coverLetters";
import {
  APPROVAL_LABELS,
  approveApplication,
  getReview,
  rejectApplication,
  requestReview,
  type ReviewPackage,
  type Verification,
} from "@/lib/api/review";

export function VerificationResult({ v }: { v: Verification }) {
  const counts = Object.entries(v.counts)
    .filter(([, n]) => n > 0)
    .map(([verdict, n]) => `${n} ${verdict.replace("_", " ")}`)
    .join(", ");
  return (
    <div className="mt-3 space-y-1 text-sm" aria-label="Verification result">
      <p className={v.verified ? "text-emerald-800" : "text-red-700"}>
        {v.verified ? "✓ Every claim is verified against your evidence." : "✗ Not fully verified."}{" "}
        <span className="text-zinc-500">
          {v.claims_verified} verified claims
          {counts ? ` · latest check: ${counts}` : ""}
          {v.checked_at ? ` · ${formatDateTime(v.checked_at)}` : " · never checked"}
        </span>
      </p>
      {v.unverified.length ? (
        <ul className="list-disc pl-5 text-red-700">
          {v.unverified.map((c) => (
            <li key={c.text}>
              “{c.text}” ({c.status.replace("_", " ")}){c.reason ? `: ${c.reason}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className="break-words">{value || "—"}</dd>
    </>
  );
}

export function ReviewPage({ applicationId }: { applicationId: string }) {
  const [review, setReview] = useState<ReviewPackage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    getReview(applicationId).then(
      (found) => !cancelled && setReview(found),
      (e: unknown) =>
        !cancelled && setError(e instanceof ApiError ? e.message : "Could not load the review."),
    );
    return () => {
      cancelled = true;
    };
  }, [applicationId]);

  async function act(call: () => Promise<ReviewPackage>) {
    setBusy(true);
    setError(null);
    try {
      setReview(await call());
      setConfirmed(false);
      setNote("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
      // The content may have changed: show the current version.
      getReview(applicationId).then(setReview, () => undefined);
    } finally {
      setBusy(false);
    }
  }

  if (!review) {
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

  const r = review;
  const blockers = r.issues.filter((i) => i.severity === "blocker");
  const warnings = r.issues.filter((i) => i.severity === "warning");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link
            href={`/applications/${r.application_id}`}
            className="text-sm text-zinc-500 hover:underline"
          >
            ← {r.job.title}, {r.job.company}
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Review before submitting
          </h1>
          <p className="max-w-3xl text-sm text-zinc-600 dark:text-zinc-400">
            Check everything below. Opening this page doesn&apos;t approve anything: approval is a
            separate, explicit step, and it covers exactly this version. If anything changes
            afterwards, your approval is withdrawn and you review it again.
          </p>
        </div>
        <span
          aria-label="Approval state"
          className="rounded-full bg-zinc-900 px-3 py-1 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
        >
          {APPROVAL_LABELS[r.approval_state]}
        </span>
      </div>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      ) : null}

      <Card id="issues" title="Missing or uncertain">
        {r.issues.length === 0 ? (
          <EmptyState>Nothing missing or uncertain.</EmptyState>
        ) : (
          <ul className="space-y-1 text-sm">
            {blockers.map((i) => (
              <li key={i.message} className="text-red-700">
                ✗ {i.message}
              </li>
            ))}
            {warnings.map((i) => (
              <li key={i.message} className="text-amber-800">
                ! {i.message}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card id="job" title="Job and company">
        <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[10rem_1fr]">
          <Row label="Job" value={r.job.title} />
          <Row label="Company" value={r.job.company} />
          <Row label="Location" value={r.job.location} />
          <Row label="Apply at" value={r.job.url} />
        </dl>
      </Card>

      <Card id="personal" title="Personal information">
        <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[10rem_1fr]">
          <Row label="Name" value={r.personal.full_name} />
          <Row
            label="Email"
            value={`${r.personal.email}${r.personal.email_source === "account" ? " (account email)" : ""}`}
          />
          <Row label="Phone" value={r.personal.phone} />
          <Row label="Location" value={r.personal.location} />
          <Row label="LinkedIn" value={r.personal.linkedin} />
        </dl>
      </Card>

      <Card
        id="resume"
        title="Resume"
        description={r.resume ? `Version ${r.resume.version}` : undefined}
      >
        {r.resume ? (
          <>
            <ResumePreview content={r.resume.content} evidence={{}} showSources={false} />
            <VerificationResult v={r.resume.verification} />
          </>
        ) : (
          <EmptyState>No resume attached.</EmptyState>
        )}
      </Card>

      <Card
        id="cover-letter"
        title="Cover letter"
        description={r.cover_letter ? `Version ${r.cover_letter.version}` : undefined}
      >
        {r.cover_letter ? (
          <>
            <div className="space-y-2 text-sm text-zinc-800 dark:text-zinc-200">
              <p>{r.cover_letter.content.greeting}</p>
              {r.cover_letter.content.paragraphs.map((p, n) => (
                <p key={n}>{paragraphText(p)}</p>
              ))}
              <p>{r.cover_letter.content.closing}</p>
            </div>
            <VerificationResult v={r.cover_letter.verification} />
          </>
        ) : (
          <EmptyState>No cover letter (optional).</EmptyState>
        )}
      </Card>

      <Card id="answers" title="Application answers">
        {r.answers.length ? (
          <ol className="space-y-4 text-sm">
            {r.answers.map((a) => (
              <li key={a.id}>
                <p className="font-medium">
                  {a.question}{" "}
                  {a.approved ? <Badge>Approved</Badge> : <Badge tone="ai">Not approved</Badge>}
                </p>
                <p className="mt-1 whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">
                  {a.answer}
                </p>
                <VerificationResult v={a.verification} />
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState>No application questions.</EmptyState>
        )}
      </Card>

      <Card id="decision" title="Your decision">
        {r.approval_state === "submitted" ? (
          <p role="status" className="text-sm text-emerald-800">
            Submitted on {formatDateTime(r.submitted_at)}.
          </p>
        ) : null}
        {r.approval && r.approval_state === "approved" ? (
          <p role="status" className="text-sm text-emerald-800">
            Approved by {r.approval.reviewer} on {formatDateTime(r.approval.approved_at)} (version{" "}
            {r.approval.version}). It can be submitted as long as nothing changes.
          </p>
        ) : null}
        {r.can_request_review ? (
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => void act(() => requestReview(r.application_id))}
          >
            Mark ready for review
          </Button>
        ) : null}
        {r.approval_state === "ready_for_review" ? (
          <div className="space-y-3">
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={confirmed}
                disabled={!r.can_approve}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>
                I&apos;ve reviewed the job, my resume, cover letter, answers, personal information
                and verification results above, and I approve this version of the application.
              </span>
            </label>
            <Field id="decision-note" label="Note (optional)">
              <input
                id="decision-note"
                className={inputClass}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                disabled={busy || !confirmed || !r.can_approve}
                onClick={() =>
                  void act(() => approveApplication(r.application_id, r.content_hash, note))
                }
              >
                Approve application
              </Button>
              <Button
                variant="danger"
                disabled={busy}
                onClick={() => void act(() => rejectApplication(r.application_id, note))}
              >
                Reject
              </Button>
            </div>
            {!r.can_approve ? (
              <p className="text-sm text-amber-800">Resolve the items marked ✗ before approving.</p>
            ) : null}
          </div>
        ) : null}
        {r.approval_state === "approved" ? (
          <Button
            variant="danger"
            className="mt-3"
            disabled={busy}
            onClick={() => void act(() => rejectApplication(r.application_id))}
          >
            Withdraw approval
          </Button>
        ) : null}
        {r.submit_blockers.length && r.approval_state !== "submitted" ? (
          <ul className="mt-3 list-disc pl-5 text-sm text-zinc-600" aria-label="Submit blockers">
            {r.submit_blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        ) : null}
      </Card>

      <Card id="audit" title="Audit log" description="Every review, decision and submission.">
        <ol className="space-y-2 text-sm" aria-label="Audit events">
          {r.events.map((e) => (
            <li key={e.id} className="flex flex-col gap-1 sm:flex-row sm:gap-3">
              <span className="w-36 shrink-0 text-xs text-zinc-500">{formatDateTime(e.at)}</span>
              <span className="w-28 shrink-0">
                <Badge tone={e.actor === "user" ? "lock" : "neutral"}>
                  {e.actor === "user"
                    ? (e.user ?? "You")
                    : e.actor === "automation"
                      ? "Browser"
                      : "CareerPilot"}
                </Badge>
              </span>
              <span>{e.message}</span>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}
