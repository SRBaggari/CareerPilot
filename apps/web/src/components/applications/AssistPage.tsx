"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState, Field, inputClass } from "@/components/ui";
import { formatDateTime, getApplication, type Application } from "@/lib/api/applications";
import {
  cancelRun,
  listRuns,
  provideInputs,
  RUN_LABELS,
  startRun,
  submitRun,
  type AssistedRun,
  type AuditEvent,
  type Review,
  type ReviewFile,
} from "@/lib/api/assist";
import { ApiError } from "@/lib/api/client";

const kb = (bytes: number) => `${(bytes / 1024).toFixed(1)} KB`;

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className="break-words text-zinc-900 dark:text-zinc-100">{value || "—"}</dd>
    </>
  );
}

function FileSummary({ file, label }: { file: ReviewFile; label: string }) {
  return (
    <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1 text-sm" aria-label={label}>
      <Row label="File" value={file.file_name} />
      <Row label="Version" value={`Approved version ${file.version}`} />
      <Row label="Size" value={kb(file.size_bytes)} />
      <Row label="Form field" value={file.field_label} />
      <dt className="text-zinc-500">SHA-256</dt>
      <dd className="font-mono text-xs break-all text-zinc-600 dark:text-zinc-400">
        {file.sha256}
      </dd>
    </dl>
  );
}

/** Exactly what will be submitted, as read back from the filled form. */
export function ReviewSections({ review }: { review: Review }) {
  const p = review.personal;
  return (
    <div className="space-y-4">
      <Card
        title="Destination website"
        description="Where the application will be sent."
        id="review-destination"
      >
        <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1 text-sm">
          <Row label="Website" value={review.destination.host} />
          <Row label="Application page" value={review.destination.url} />
          <Row label="Form sends to" value={review.destination.form_action} />
        </dl>
      </Card>
      <Card title="Personal information" id="review-personal">
        <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1 text-sm">
          <Row label="First name" value={p.first_name} />
          <Row label="Last name" value={p.last_name} />
          <Row label="Email" value={p.email} />
          <Row label="Phone" value={p.phone} />
          <Row label="Location" value={p.location} />
          <Row label="LinkedIn" value={p.linkedin} />
        </dl>
      </Card>
      <Card title="Resume" id="review-resume">
        <FileSummary file={review.resume} label="Resume file" />
      </Card>
      <Card title="Cover letter" id="review-cover-letter">
        {review.cover_letter ? (
          <FileSummary file={review.cover_letter} label="Cover letter file" />
        ) : (
          <EmptyState>No cover letter will be sent.</EmptyState>
        )}
      </Card>
      <Card
        title="Application answers"
        description="Your approved answers, exactly as entered in the form."
        id="review-answers"
      >
        {review.answers.length ? (
          <ol className="space-y-3 text-sm">
            {review.answers.map((a) => (
              <li key={a.field_id}>
                <p className="font-medium text-zinc-900 dark:text-zinc-100">{a.question}</p>
                <p className="mt-1 whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">
                  {a.answer}
                </p>
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState>The form doesn&apos;t ask any questions you have answers for.</EmptyState>
        )}
      </Card>
      <Card title="Additional fields" id="review-additional">
        {review.additional_fields.length ? (
          <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
            {review.additional_fields.map((f) => (
              <Row key={f.field_id} label={f.label} value={`${f.value} (${f.source})`} />
            ))}
          </dl>
        ) : (
          <EmptyState>None.</EmptyState>
        )}
        {review.left_blank.length ? (
          <p className="mt-3 text-sm text-zinc-500">
            Left blank (optional): {review.left_blank.join(", ")}.
          </p>
        ) : null}
      </Card>
    </div>
  );
}

export function AuditLog({ events }: { events: AuditEvent[] }) {
  return (
    <ol className="space-y-2 text-sm" aria-label="Audit events">
      {events.map((e) => (
        <li key={e.id} className="flex gap-3">
          <span className="w-36 shrink-0 text-xs text-zinc-500">{formatDateTime(e.at)}</span>
          <span className="w-20 shrink-0">
            <Badge tone={e.actor === "user" ? "lock" : "neutral"}>
              {e.actor === "user" ? "You" : e.actor === "automation" ? "Browser" : "CareerPilot"}
            </Badge>
          </span>
          <span className="text-zinc-800 dark:text-zinc-200">{e.message}</span>
        </li>
      ))}
    </ol>
  );
}

export function AssistPage({ applicationId }: { applicationId: string }) {
  const [app, setApp] = useState<Application | null>(null);
  const [run, setRun] = useState<AssistedRun | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getApplication(applicationId), listRuns(applicationId)]).then(
      ([found, runs]) => {
        if (cancelled) return;
        setApp(found);
        setRun(runs[0] ?? null);
        setLoaded(true);
      },
      (e: unknown) =>
        !cancelled &&
        setError(e instanceof ApiError ? e.message : "Could not load the application."),
    );
    return () => {
      cancelled = true;
    };
  }, [applicationId]);

  async function act(label: string, call: () => Promise<AssistedRun>) {
    setBusy(label);
    setError(null);
    setConfirmed(false);
    try {
      const updated = await call();
      setRun(updated);
      setInputs(updated.inputs);
      if (updated.status === "submitted") setApp(await getApplication(applicationId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  if (!loaded || !app) {
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

  const approved = app.approved_at !== null;
  const open = run && ["needs_input", "awaiting_review"].includes(run.status);
  const canStart = approved && !app.applied_at && !open && run?.status !== "submitted";
  const review = run?.status === "awaiting_review" ? run.review : null;
  const inputProblems =
    run?.status === "needs_input" ? run.problems.filter((p) => p.needs_input) : [];
  const otherProblems =
    run?.status === "needs_input" ? run.problems.filter((p) => !p.needs_input) : [];

  return (
    <div className="space-y-6">
      <div>
        <Link href={`/applications/${app.id}`} className="text-sm text-zinc-500 hover:underline">
          ← {app.position}, {app.company}
        </Link>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Application review
        </h1>
        <p className="max-w-3xl text-sm text-zinc-600 dark:text-zinc-400">
          CareerPilot opens the application page, fills it from your approved resume, cover letter
          and answers, then pauses and shows you exactly what would be sent. Nothing is submitted
          unless you confirm. It never signs in for you, solves CAPTCHAs, or works around a
          site&apos;s restrictions: it stops and tells you why.
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

      {!approved ? (
        <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
          Approve the application first. CareerPilot only fills applications you approved.
        </p>
      ) : null}

      {run ? (
        <p className="text-sm">
          <span className="text-zinc-500">Status:</span>{" "}
          <strong aria-label="Run status">{RUN_LABELS[run.status]}</strong>
          {" · "}
          <span className="text-zinc-500">{run.destination_host}</span>
        </p>
      ) : null}

      {run && ["stopped", "failed"].includes(run.status) && run.stop_reason ? (
        <div
          role="alert"
          className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900"
        >
          <p className="font-medium">CareerPilot stopped.</p>
          <p className="mt-1">{run.stop_reason}</p>
        </div>
      ) : null}

      {run?.status === "submitted" ? (
        <div
          role="status"
          className="rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-900"
        >
          <p className="font-medium">Submitted after your confirmation.</p>
          <p className="mt-1">
            {run.destination_host} confirmed it on {formatDateTime(run.submitted_at)}
            {run.confirmation_reference ? ` (reference ${run.confirmation_reference})` : ""}.
          </p>
        </div>
      ) : null}

      {canStart ? (
        <Button
          variant="primary"
          disabled={busy !== null}
          onClick={() => void act("start", () => startRun(app.id, inputs))}
        >
          {busy === "start"
            ? "Filling the form…"
            : run
              ? "Try again"
              : "Fill the application for my review"}
        </Button>
      ) : null}

      {run?.status === "needs_input" ? (
        <Card
          id="needs-input"
          title="CareerPilot needs you"
          description="It only fills what's in your approved records and never guesses. Nothing has been filled or submitted."
        >
          {otherProblems.length ? (
            <ul className="mb-4 list-disc space-y-1 pl-5 text-sm text-amber-900">
              {otherProblems.map((p) => (
                <li key={p.field_id}>
                  <strong>{p.label}:</strong> {p.message}
                </li>
              ))}
            </ul>
          ) : null}
          {inputProblems.length ? (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                void act("inputs", () => provideInputs(run.id, inputs));
              }}
            >
              {inputProblems.map((p) => (
                <Field
                  key={p.field_id}
                  id={`input-${p.field_id}`}
                  label={p.label}
                  required={p.required}
                  hint={p.message}
                >
                  {p.options.length ? (
                    <select
                      id={`input-${p.field_id}`}
                      className={inputClass}
                      value={inputs[p.field_id] ?? ""}
                      onChange={(e) => setInputs({ ...inputs, [p.field_id]: e.target.value })}
                    >
                      <option value="">Choose…</option>
                      {p.options.map((o) => (
                        <option key={o}>{o}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id={`input-${p.field_id}`}
                      className={inputClass}
                      value={inputs[p.field_id] ?? ""}
                      onChange={(e) => setInputs({ ...inputs, [p.field_id]: e.target.value })}
                    />
                  )}
                </Field>
              ))}
              <Button type="submit" variant="primary" disabled={busy !== null}>
                {busy === "inputs" ? "Filling the form…" : "Continue"}
              </Button>
            </form>
          ) : (
            <Button
              variant="primary"
              disabled={busy !== null}
              onClick={() => void act("start", () => startRun(app.id, inputs))}
            >
              I&apos;ve fixed this, try again
            </Button>
          )}
        </Card>
      ) : null}

      {review && run ? (
        <>
          <p
            role="status"
            className="rounded-lg border border-sky-300 bg-sky-50 p-4 text-sm font-medium text-sky-900"
          >
            Nothing has been submitted yet. Check everything below: this is exactly what CareerPilot
            will send to {review.destination.host}.
          </p>
          <ReviewSections review={review} />
          <Card id="confirm" title="Confirm submission">
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>
                I&apos;ve reviewed everything above and want CareerPilot to submit exactly this
                application to {review.destination.host}.
              </span>
            </label>
            <div className="mt-4 flex gap-2">
              <Button
                variant="primary"
                disabled={!confirmed || busy !== null || !run.review_hash}
                onClick={() =>
                  void act("submit", () => submitRun(run.id, run.review_hash as string))
                }
              >
                {busy === "submit" ? "Submitting…" : "Submit application"}
              </Button>
              <Button
                variant="ghost"
                disabled={busy !== null}
                onClick={() => void act("cancel", () => cancelRun(run.id))}
              >
                Cancel
              </Button>
            </div>
          </Card>
        </>
      ) : null}

      {run ? (
        <Card id="audit-log" title="Audit log" description="Every action taken, in order.">
          <AuditLog events={run.events} />
        </Card>
      ) : null}
    </div>
  );
}
