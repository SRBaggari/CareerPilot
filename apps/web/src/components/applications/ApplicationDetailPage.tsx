"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState, Field, inputClass } from "@/components/ui";
import {
  addFollowUp,
  addInterview,
  approveApplication,
  changeStatus,
  formatDate,
  formatDateTime,
  getApplication,
  STATUS_LABELS,
  updateApplication,
  updateFollowUp,
  updateInterview,
  type Application,
  type ApplicationStatus,
  type InterviewType,
} from "@/lib/api/applications";
import { ApiError } from "@/lib/api/client";

const INTERVIEW_TYPES: InterviewType[] = [
  "phone_screen",
  "technical",
  "behavioral",
  "take_home",
  "onsite",
  "panel",
  "other",
];
const KIND_TONES: Record<string, string> = {
  status: "bg-zinc-400",
  approval: "bg-emerald-500",
  interview: "bg-sky-500",
  follow_up: "bg-amber-500",
  document: "bg-violet-500",
  answer: "bg-violet-400",
  created: "bg-zinc-300",
};

/** Everything that happened, newest first; scheduled items are marked as upcoming. */
export function Timeline({ events }: { events: Application["timeline"] }) {
  return (
    <ol
      className="space-y-3 border-l border-zinc-200 pl-4 dark:border-zinc-800"
      aria-label="Timeline"
    >
      {events.map((e, n) => (
        <li key={`${e.at}-${n}`} className="relative text-sm">
          <span
            className={`absolute top-1.5 -left-[21px] h-2.5 w-2.5 rounded-full ${KIND_TONES[e.kind] ?? "bg-zinc-400"}`}
          />
          <p className="font-medium text-zinc-900 dark:text-zinc-100">
            {e.title} {e.upcoming ? <Badge tone="lock">Upcoming</Badge> : null}
          </p>
          <p className="text-xs text-zinc-500">{formatDateTime(e.at)}</p>
          {e.detail ? <p className="text-xs text-zinc-600 dark:text-zinc-400">{e.detail}</p> : null}
        </li>
      ))}
    </ol>
  );
}

export function ApplicationDetailPage({ applicationId }: { applicationId: string }) {
  const [app, setApp] = useState<Application | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [nextStatus, setNextStatus] = useState<ApplicationStatus | "">("");
  const [statusNote, setStatusNote] = useState("");
  const [appliedOn, setAppliedOn] = useState("");
  const [notes, setNotes] = useState("");
  const [interview, setInterview] = useState({
    type: "phone_screen" as InterviewType,
    when: "",
    where: "",
  });
  const [followUp, setFollowUp] = useState({ subject: "", due: "" });

  useEffect(() => {
    let cancelled = false;
    getApplication(applicationId).then(
      (found) => {
        if (cancelled) return;
        setApp(found);
        setNotes(found.notes ?? "");
      },
      (e: unknown) =>
        !cancelled &&
        setError(e instanceof ApiError ? e.message : "Could not load the application."),
    );
    return () => {
      cancelled = true;
    };
  }, [applicationId]);

  async function run(call: () => Promise<Application>) {
    setBusy(true);
    setError(null);
    try {
      const updated = await call();
      setApp(updated);
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!app) {
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

  const canApprove = app.approval_blockers.length === 0;
  const approvedAlready = app.approved_at !== null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/applications" className="text-sm text-zinc-500 hover:underline">
            ← Applications
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            {app.position}
          </h1>
          <p className="text-zinc-600 dark:text-zinc-400">
            {app.company}
            {app.location ? ` · ${app.location}` : ""}
          </p>
        </div>
        <span
          aria-label="Status"
          className="rounded-full bg-zinc-900 px-3 py-1 text-sm font-medium text-white dark:bg-zinc-100 dark:text-zinc-900"
        >
          {STATUS_LABELS[app.status]}
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

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-6">
          <Card title="Details">
            <dl
              className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm"
              aria-label="Application details"
            >
              <dt className="text-zinc-500">Job posting</dt>
              <dd>
                {app.job_url ? (
                  <a
                    href={app.job_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline"
                  >
                    Open posting
                  </a>
                ) : (
                  "—"
                )}{" "}
                ·{" "}
                <Link href={`/jobs/${app.job_id}`} className="underline">
                  Analysis
                </Link>
              </dd>
              <dt className="text-zinc-500">Discovered</dt>
              <dd>{formatDate(app.discovered_at)}</dd>
              <dt className="text-zinc-500">Approved</dt>
              <dd>{formatDateTime(app.approved_at)}</dd>
              <dt className="text-zinc-500">Applied</dt>
              <dd>{formatDate(app.applied_at)}</dd>
            </dl>
          </Card>

          <Card
            title="Status"
            description="Nothing counts as submitted until you've approved the application and submitted it yourself."
          >
            <div className="space-y-3">
              <div className="flex flex-wrap items-end gap-2">
                <Field id="next-status" label="Move to">
                  <select
                    id="next-status"
                    className={inputClass}
                    value={nextStatus}
                    onChange={(e) => setNextStatus(e.target.value as ApplicationStatus)}
                  >
                    <option value="">Choose…</option>
                    {app.allowed_statuses.map((s) => (
                      <option key={s} value={s}>
                        {STATUS_LABELS[s]}
                      </option>
                    ))}
                  </select>
                </Field>
                {nextStatus === "submitted" ? (
                  <Field id="applied-on" label="Date you applied">
                    <input
                      id="applied-on"
                      type="date"
                      className={inputClass}
                      value={appliedOn}
                      onChange={(e) => setAppliedOn(e.target.value)}
                    />
                  </Field>
                ) : null}
                <Field id="status-note" label="Note (optional)" className="flex-1">
                  <input
                    id="status-note"
                    className={inputClass}
                    value={statusNote}
                    onChange={(e) => setStatusNote(e.target.value)}
                  />
                </Field>
                <Button
                  variant="primary"
                  disabled={busy || !nextStatus}
                  onClick={() =>
                    void run(() =>
                      changeStatus(app.id, nextStatus as ApplicationStatus, {
                        ...(statusNote ? { note: statusNote } : {}),
                        ...(nextStatus === "submitted" && appliedOn
                          ? { submitted_on: appliedOn }
                          : {}),
                      }),
                    ).then((done) => done && (setNextStatus(""), setStatusNote("")))
                  }
                >
                  Update status
                </Button>
              </div>
            </div>
          </Card>

          <Card
            title="Approval"
            description="Your explicit sign-off. It submits nothing: after approving, submit the application yourself and record it as submitted, or let CareerPilot fill it in on a supported site and submit only after you confirm the final review."
          >
            <ul className="space-y-1 text-sm" aria-label="Readiness">
              {app.readiness.map((item) => (
                <li key={item.label}>
                  <span className={item.ok ? "text-emerald-700" : "text-red-700"}>
                    {item.ok ? "✓" : "✗"}
                  </span>{" "}
                  <strong>{item.label}:</strong> {item.detail}
                </li>
              ))}
            </ul>
            {approvedAlready ? (
              <div className="mt-3 space-y-2">
                <p className="text-sm text-emerald-800" role="status">
                  Approved by you on {formatDateTime(app.approved_at)}.
                </p>
                {!app.applied_at ? (
                  <p className="text-sm">
                    <Link href={`/applications/${app.id}/assist`} className="font-medium underline">
                      Fill it in on the site for my review
                    </Link>{" "}
                    <span className="text-zinc-500">
                      (supported sites only; nothing is submitted until you confirm)
                    </span>
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="mt-3 space-y-2">
                {!canApprove ? (
                  <ul
                    className="list-disc pl-5 text-sm text-amber-800"
                    aria-label="Approval blockers"
                  >
                    {app.approval_blockers.map((b) => (
                      <li key={b}>{b}</li>
                    ))}
                  </ul>
                ) : null}
                <Button
                  variant="primary"
                  disabled={busy || !canApprove}
                  onClick={() => void run(() => approveApplication(app.id))}
                >
                  Approve application
                </Button>
              </div>
            )}
          </Card>

          <Card title="Documents and answers">
            <ul className="space-y-2 text-sm">
              <li>
                <strong>Resume:</strong>{" "}
                {app.resume ? (
                  <>
                    <Link href={`/jobs/${app.job_id}/resume`} className="underline">
                      Version {app.resume.version}
                    </Link>{" "}
                    ({app.resume.status.replace("_", " ")})
                    {app.resume.newer_version ? (
                      <span className="text-amber-800">
                        {" "}
                        · version {app.resume.newer_version} is newer
                      </span>
                    ) : null}
                  </>
                ) : (
                  <Link href={`/jobs/${app.job_id}/resume`} className="underline">
                    None attached: tailor one
                  </Link>
                )}
              </li>
              <li>
                <strong>Cover letter:</strong>{" "}
                {app.cover_letter ? (
                  <>
                    <Link href={`/jobs/${app.job_id}/cover-letter`} className="underline">
                      Version {app.cover_letter.version}
                    </Link>{" "}
                    ({app.cover_letter.status.replace("_", " ")})
                  </>
                ) : (
                  <Link href={`/jobs/${app.job_id}/cover-letter`} className="underline">
                    None attached (optional)
                  </Link>
                )}
              </li>
              <li>
                <strong>Application answers:</strong>{" "}
                <Link href={`/jobs/${app.job_id}/questions`} className="underline">
                  {app.answers_total
                    ? `${app.answers_approved} of ${app.answers_total} approved`
                    : "None"}
                </Link>
                {app.answers.length ? (
                  <ul
                    className="mt-1 list-disc pl-5 text-xs text-zinc-600 dark:text-zinc-400"
                    aria-label="Answers"
                  >
                    {app.answers.map((a) => (
                      <li key={a.id}>
                        {a.question} — {a.approved ? "approved" : a.status.replace("_", " ")}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            </ul>
          </Card>

          <Card title="Notes">
            <label htmlFor="app-notes" className="sr-only">
              Notes
            </label>
            <textarea
              id="app-notes"
              rows={4}
              className={inputClass}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
            <Button
              size="sm"
              className="mt-2"
              disabled={busy || notes === (app.notes ?? "")}
              onClick={() => void run(() => updateApplication(app.id, { notes: notes || null }))}
            >
              Save notes
            </Button>
          </Card>

          <Card title="Interviews">
            {app.interviews.length ? (
              <ul className="space-y-2 text-sm" aria-label="Interviews">
                {app.interviews.map((i) => (
                  <li key={i.id} className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      <strong>{i.interview_type.replace("_", " ")}</strong> ·{" "}
                      {formatDateTime(i.scheduled_at)} · {i.status.replace("_", " ")}
                      {i.location ? ` · ${i.location}` : ""}
                    </span>
                    {i.status === "scheduled" ? (
                      <span className="flex gap-1">
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            void run(() => updateInterview(app.id, i.id, { status: "completed" }))
                          }
                        >
                          Mark completed
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            void run(() => updateInterview(app.id, i.id, { status: "cancelled" }))
                          }
                        >
                          Cancel
                        </Button>
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState>No interviews yet.</EmptyState>
            )}
            {app.applied_at ? (
              <form
                className="mt-3 flex flex-wrap items-end gap-2"
                aria-label="Add interview"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(() =>
                    addInterview(app.id, {
                      interview_type: interview.type,
                      scheduled_at: interview.when ? new Date(interview.when).toISOString() : null,
                      location: interview.where || null,
                    }),
                  ).then(
                    (done) => done && setInterview({ type: "phone_screen", when: "", where: "" }),
                  );
                }}
              >
                <Field id="interview-type" label="Type">
                  <select
                    id="interview-type"
                    className={inputClass}
                    value={interview.type}
                    onChange={(e) =>
                      setInterview({ ...interview, type: e.target.value as InterviewType })
                    }
                  >
                    {INTERVIEW_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t.replace("_", " ")}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field id="interview-when" label="When">
                  <input
                    id="interview-when"
                    type="datetime-local"
                    className={inputClass}
                    value={interview.when}
                    onChange={(e) => setInterview({ ...interview, when: e.target.value })}
                  />
                </Field>
                <Field id="interview-where" label="Where (optional)">
                  <input
                    id="interview-where"
                    className={inputClass}
                    value={interview.where}
                    onChange={(e) => setInterview({ ...interview, where: e.target.value })}
                  />
                </Field>
                <Button type="submit" size="sm" disabled={busy}>
                  Add interview
                </Button>
              </form>
            ) : (
              <p className="mt-2 text-xs text-zinc-500">
                Interviews can be added once you&apos;ve recorded the submission.
              </p>
            )}
          </Card>

          <Card
            title="Follow-ups"
            description="Reminders for you. CareerPilot never contacts anyone."
          >
            {app.follow_ups.length ? (
              <ul className="space-y-2 text-sm" aria-label="Follow-ups">
                {app.follow_ups.map((f) => (
                  <li key={f.id} className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      {f.overdue ? <Badge tone="ai">Overdue</Badge> : null} {f.subject} · due{" "}
                      {formatDate(f.due_at)} · {f.status}
                    </span>
                    {f.status === "pending" ? (
                      <span className="flex gap-1">
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            void run(() => updateFollowUp(app.id, f.id, { status: "done" }))
                          }
                        >
                          Done
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            void run(() => updateFollowUp(app.id, f.id, { status: "skipped" }))
                          }
                        >
                          Skip
                        </Button>
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState>No follow-ups.</EmptyState>
            )}
            <form
              className="mt-3 flex flex-wrap items-end gap-2"
              aria-label="Add follow-up"
              onSubmit={(e) => {
                e.preventDefault();
                void run(() =>
                  addFollowUp(app.id, {
                    subject: followUp.subject,
                    due_at: new Date(followUp.due).toISOString(),
                  }),
                ).then((done) => done && setFollowUp({ subject: "", due: "" }));
              }}
            >
              <Field id="follow-up-subject" label="Reminder">
                <input
                  id="follow-up-subject"
                  className={inputClass}
                  value={followUp.subject}
                  onChange={(e) => setFollowUp({ ...followUp, subject: e.target.value })}
                />
              </Field>
              <Field id="follow-up-due" label="Due">
                <input
                  id="follow-up-due"
                  type="date"
                  className={inputClass}
                  value={followUp.due}
                  onChange={(e) => setFollowUp({ ...followUp, due: e.target.value })}
                />
              </Field>
              <Button type="submit" size="sm" disabled={busy || !followUp.subject || !followUp.due}>
                Add follow-up
              </Button>
            </form>
          </Card>
        </div>

        <Card title="Timeline">
          <Timeline events={app.timeline} />
        </Card>
      </div>
    </div>
  );
}
