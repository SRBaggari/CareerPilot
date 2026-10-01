"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge, Card, EmptyState } from "@/components/ui";
import { listJobs, type JobSummary } from "@/lib/api/jobs";
import { JOB_TYPE_LABELS, label, WORK_MODE_LABELS } from "@/lib/profile/options";

import { NewJobForm } from "./NewJobForm";

export function JobsList({ jobs }: { jobs: JobSummary[] }) {
  if (jobs.length === 0) return <EmptyState>No jobs analyzed yet.</EmptyState>;
  return (
    <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
      {jobs.map((job) => (
        <li key={job.id} className="py-3 first:pt-0 last:pb-0">
          <Link href={`/jobs/${job.id}`} className="group block">
            <p className="font-medium text-zinc-900 group-hover:underline dark:text-zinc-100">
              {job.title}
            </p>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {[
                job.company_name,
                job.location,
                label(WORK_MODE_LABELS, job.workplace_type),
                label(JOB_TYPE_LABELS, job.employment_type),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            <span className="mt-1 flex gap-1">
              <Badge>{job.requirement_counts.required} required</Badge>
              <Badge>{job.requirement_counts.preferred} preferred</Badge>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function JobsPage() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listJobs().then(
      (items) => !cancelled && setJobs(items),
      () => !cancelled && setJobs([]),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Jobs
          </h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Every job you analyzed or imported. Open one to match, tailor and apply.
          </p>
        </div>
        <Link
          href="/analyze"
          className="inline-flex items-center rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          Analyze a job
        </Link>
      </div>
      <Card id="your-jobs" title="Your jobs">
        {jobs === null ? (
          <p role="status" className="text-sm text-zinc-500">
            Loading…
          </p>
        ) : (
          <JobsList jobs={jobs} />
        )}
      </Card>
    </main>
  );
}

/** Job Analysis: paste a posting (or enter it) and see what it actually requires. */
export function JobAnalysisPage() {
  const router = useRouter();
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Job Analysis
        </h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Paste a job description. CareerPilot reads what the posting states and never infers
          requirements that aren&apos;t there.
        </p>
      </div>
      <Card id="analyze" title="Analyze a job">
        <NewJobForm onCreated={(job) => router.push(`/jobs/${job.id}`)} />
      </Card>
    </main>
  );
}
