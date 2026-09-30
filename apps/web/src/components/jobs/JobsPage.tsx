"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
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
  const router = useRouter();
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
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/jobs" />
      <main className="mx-auto grid w-full max-w-6xl flex-1 gap-6 px-6 py-8 lg:grid-cols-[1fr_20rem]">
        <Card
          title="Analyze a job"
          description="CareerPilot reads what the posting states - it never infers requirements that aren't there."
        >
          <NewJobForm onCreated={(job) => router.push(`/jobs/${job.id}`)} />
        </Card>
        <Card title="Your jobs">
          {jobs === null ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : (
            <JobsList jobs={jobs} />
          )}
        </Card>
      </main>
    </div>
  );
}
