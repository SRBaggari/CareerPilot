"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { Badge, Card, EmptyState } from "@/components/ui";
import { formatDate } from "@/lib/api/applications";
import { coverLetterDownloadUrl, getLatestCoverLetter } from "@/lib/api/coverLetters";
import { listJobs, type JobSummary } from "@/lib/api/jobs";
import { listResumes, type Resume } from "@/lib/api/resumes";
import { downloadUrl, getLatestTailoredResume } from "@/lib/api/tailoredResumes";

type Doc = { id: string; version: number; status: string; updated_at: string };
type Row = { job: JobSummary; doc: Doc | null; failed: boolean };

export const DOC_STATUS: Record<string, { label: string; tone: "neutral" | "ai" | "lock" }> = {
  draft: { label: "Draft", tone: "neutral" },
  verification_failed: { label: "Needs fixing", tone: "ai" },
  verified: { label: "Verified", tone: "lock" },
  approved: { label: "Approved", tone: "lock" },
  archived: { label: "Archived", tone: "neutral" },
};

/** The latest document of one kind for every job, with where to open or create it. */
function JobDocuments({
  noun,
  latest,
  path,
  download,
}: {
  noun: string;
  latest: (jobId: string) => Promise<Doc | null>;
  path: string;
  download: (id: string) => string;
}) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listJobs().then(
      async (jobs) => {
        const found = await Promise.allSettled(jobs.map((j) => latest(j.id)));
        if (cancelled) return;
        setRows(
          jobs.map((job, n) => {
            const result = found[n];
            return result.status === "fulfilled"
              ? { job, doc: result.value, failed: false }
              : { job, doc: null, failed: true };
          }),
        );
      },
      () => !cancelled && setError(true),
    );
    return () => {
      cancelled = true;
    };
  }, [latest]);

  if (error)
    return (
      <p role="alert" className="text-sm text-red-700">
        Could not load your jobs.
      </p>
    );
  if (rows === null)
    return (
      <p role="status" className="text-sm text-zinc-500">
        Loading…
      </p>
    );
  if (rows.length === 0)
    return (
      <EmptyState>
        No jobs yet.{" "}
        <Link href="/analyze" className="underline">
          Analyze a job
        </Link>{" "}
        to write a {noun} for it.
      </EmptyState>
    );
  return (
    <ul className="divide-y divide-zinc-100 dark:divide-zinc-800" aria-label={`${noun}s by job`}>
      {rows.map(({ job, doc, failed }) => {
        const status = doc ? (DOC_STATUS[doc.status] ?? DOC_STATUS.draft) : null;
        return (
          <li
            key={job.id}
            className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="truncate font-medium text-zinc-900 dark:text-zinc-100">{job.title}</p>
              <p className="text-sm text-zinc-500">
                {job.company_name}
                {doc ? ` · version ${doc.version} · ${formatDate(doc.updated_at)}` : ""}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
              {failed ? <span className="text-red-700">Couldn&apos;t load</span> : null}
              {doc ? (
                <>
                  <Link href={`/jobs/${job.id}/${path}`} className="font-medium underline">
                    Open
                  </Link>
                  <a href={download(doc.id)} className="underline">
                    PDF
                  </a>
                </>
              ) : (
                <Link href={`/jobs/${job.id}/${path}`} className="font-medium underline">
                  Create
                </Link>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Page({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          {title}
        </h1>
        <p className="max-w-3xl text-sm text-zinc-600 dark:text-zinc-400">{description}</p>
      </div>
      {children}
    </main>
  );
}

const latestResume = (jobId: string) => getLatestTailoredResume(jobId);
const latestLetter = (jobId: string) => getLatestCoverLetter(jobId);
const resumePdf = (id: string) => downloadUrl(id, "pdf");
const letterPdf = (id: string) => coverLetterDownloadUrl(id, "pdf");

function MasterResumes() {
  const [resumes, setResumes] = useState<Resume[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    listResumes().then(
      (found) => !cancelled && setResumes(found),
      () => !cancelled && setResumes([]),
    );
    return () => {
      cancelled = true;
    };
  }, []);
  if (resumes === null)
    return (
      <p role="status" className="text-sm text-zinc-500">
        Loading…
      </p>
    );
  if (resumes.length === 0)
    return (
      <EmptyState>
        No resume uploaded yet.{" "}
        <Link href="/profile" className="underline">
          Upload it in your profile
        </Link>{" "}
        so CareerPilot can build from your real experience.
      </EmptyState>
    );
  return (
    <ul className="space-y-1 text-sm" aria-label="Uploaded resumes">
      {resumes.map((r) => (
        <li key={r.id} className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{r.file_name}</span>
          {r.is_primary ? <Badge tone="lock">Primary</Badge> : null}
          <span className="text-zinc-500">
            {r.parse_status === "parsed" ? "read" : r.parse_status} · {formatDate(r.created_at)}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ResumeBuilderPage() {
  return (
    <Page
      title="Resume Builder"
      description="A resume tailored to each job, written only from your verified evidence. Open one to edit, re-verify or download it."
    >
      <Card
        id="master"
        title="Your uploaded resume"
        description="The source CareerPilot reads your experience from. Manage it in your profile."
      >
        <MasterResumes />
      </Card>
      <Card id="tailored" title="Tailored resumes">
        <JobDocuments noun="resume" latest={latestResume} path="resume" download={resumePdf} />
      </Card>
    </Page>
  );
}

export function CoverLettersPage() {
  return (
    <Page
      title="Cover Letters"
      description="A cover letter for each job, where every claim is checked against your evidence before you see it."
    >
      <Card id="letters" title="Cover letters">
        <JobDocuments
          noun="cover letter"
          latest={latestLetter}
          path="cover-letter"
          download={letterPdf}
        />
      </Card>
    </Page>
  );
}
