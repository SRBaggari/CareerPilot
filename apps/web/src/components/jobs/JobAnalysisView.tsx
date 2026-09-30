"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Badge, Button, Card, EmptyState } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { deleteJob, getJob, type Job, type Requirement } from "@/lib/api/jobs";
import {
  formatLongDate,
  formatSalary,
  groupRequirements,
  IMPORTANCE_LABELS,
} from "@/lib/jobs/labels";
import { JOB_TYPE_LABELS, label, WORK_MODE_LABELS } from "@/lib/profile/options";

const IMPORTANCE_STYLE = {
  required: "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900",
  preferred: "bg-sky-100 text-sky-900 dark:bg-sky-900/40 dark:text-sky-200",
  informational: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
} as const;

function ImportanceBadge({ importance }: { importance: Requirement["importance"] }) {
  return (
    <span
      className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${IMPORTANCE_STYLE[importance]}`}
    >
      {IMPORTANCE_LABELS[importance]}
    </span>
  );
}

function Source({ requirement }: { requirement: Requirement }) {
  if (!requirement.source_excerpt || requirement.source_excerpt === requirement.description)
    return null;
  return (
    <details className="mt-0.5 text-xs text-zinc-500">
      <summary className="cursor-pointer">From the job description</summary>
      <q className="italic">{requirement.source_excerpt}</q>
    </details>
  );
}

function RequirementList({ items, empty }: { items: Requirement[]; empty: string }) {
  if (items.length === 0) return <EmptyState>{empty}</EmptyState>;
  return (
    <ul className="space-y-2">
      {items.map((r) => (
        <li key={r.id} className="flex items-start gap-2 text-sm text-zinc-800 dark:text-zinc-200">
          <ImportanceBadge importance={r.importance} />
          <div className="min-w-0">
            <span>{r.description}</span>
            {r.min_years ? (
              <span className="ml-2 text-xs text-zinc-500">({Number(r.min_years)}+ years)</span>
            ) : null}
            <Source requirement={r} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function SkillList({ items, empty }: { items: Requirement[]; empty: string }) {
  const technologies = items.filter((r) => r.requirement_type === "technology");
  const statements = items.filter((r) => r.requirement_type !== "technology");
  if (items.length === 0) return <EmptyState>{empty}</EmptyState>;
  return (
    <div className="space-y-3">
      {technologies.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Technologies">
          {technologies.map((r) => (
            <li
              key={r.id}
              title={r.source_excerpt ?? undefined}
              className="rounded-full border border-zinc-300 px-2.5 py-0.5 text-sm text-zinc-800 dark:border-zinc-700 dark:text-zinc-200"
            >
              {r.description}
            </li>
          ))}
        </ul>
      ) : null}
      {statements.length > 0 ? <RequirementList items={statements} empty="" /> : null}
    </div>
  );
}

function Detail({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-zinc-500">{name}</dt>
      <dd className="text-sm text-zinc-900 dark:text-zinc-100">
        {children ?? <span className="text-zinc-400">Not stated</span>}
      </dd>
    </div>
  );
}

type State =
  { status: "loading" } | { status: "error"; message: string } | { status: "ready"; job: Job };

export function JobAnalysisView({ jobId }: { jobId: string }) {
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getJob(jobId).then(
      (job) => !cancelled && setState({ status: "ready", job }),
      (e: unknown) =>
        !cancelled &&
        setState({
          status: "error",
          message: e instanceof ApiError ? e.message : "Could not load the job.",
        }),
    );
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (state.status === "loading")
    return (
      <p role="status" className="text-sm text-zinc-500">
        Loading job…
      </p>
    );
  if (state.status === "error")
    return (
      <div
        role="alert"
        className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-800"
      >
        {state.message}{" "}
        <Link href="/jobs" className="underline">
          Back to jobs
        </Link>
      </div>
    );

  const { job } = state;
  const groups = groupRequirements(job.requirements);
  const salary = job.salary ? formatSalary(job.salary) : null;

  async function remove() {
    if (!window.confirm(`Delete "${job.title}" at ${job.company_name}?`)) return;
    try {
      await deleteJob(job.id);
      router.push("/jobs");
    } catch (e) {
      window.alert(e instanceof ApiError ? e.message : "Could not delete the job.");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/jobs" className="text-sm text-zinc-500 hover:underline">
            ← All jobs
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            {job.title}
          </h1>
          <p className="text-zinc-600 dark:text-zinc-400">{job.company_name}</p>
        </div>
        <Button variant="danger" onClick={() => void remove()}>
          Delete job
        </Button>
      </div>

      {job.analysis_warnings.length > 0 ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <p className="font-medium">Check these points</p>
          <ul className="mt-1 list-disc pl-5">
            {job.analysis_warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <Card
        id="overview"
        title="Job overview"
        description={
          job.input_method === "manual_entry"
            ? "Entered manually."
            : "Extracted from the pasted description. Only what the posting states explicitly is shown."
        }
      >
        <dl className="grid gap-4 sm:grid-cols-3">
          <Detail name="Company">{job.company_name}</Detail>
          <Detail name="Location">{job.location}</Detail>
          <Detail name="Work mode">
            {job.workplace_type ? label(WORK_MODE_LABELS, job.workplace_type) : null}
          </Detail>
          <Detail name="Employment type">
            {job.employment_type ? label(JOB_TYPE_LABELS, job.employment_type) : null}
          </Detail>
          <Detail name="Salary">
            {job.salary ? (
              <>
                {salary ?? job.salary.text}
                {salary && job.salary.text ? (
                  <span className="block text-xs text-zinc-500">“{job.salary.text}”</span>
                ) : null}
              </>
            ) : null}
          </Detail>
          <Detail name="Application deadline">
            {job.application_deadline ? formatLongDate(job.application_deadline) : null}
          </Detail>
          <Detail name="Posting">
            {job.source_url ? (
              <a
                href={job.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="break-all underline-offset-2 hover:underline"
              >
                {job.source_url.replace(/^https?:\/\//, "")}
              </a>
            ) : null}
          </Detail>
          <Detail name="Analysis">
            {job.analyzer_name === null
              ? "Manual entry"
              : job.analyzer_name === "heuristic"
                ? "Rule-based"
                : `AI (${job.analyzer_name.replace(/^llm:/, "")})`}
          </Detail>
          <Detail name="Requirements">
            <span className="flex flex-wrap gap-1">
              <Badge>{job.requirement_counts.required} required</Badge>
              <Badge>{job.requirement_counts.preferred} preferred</Badge>
            </span>
          </Detail>
        </dl>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card id="required-skills" title="Required skills">
          <SkillList items={groups.requiredSkills} empty="No required skills are stated." />
        </Card>
        <Card id="preferred-skills" title="Preferred skills">
          <SkillList items={groups.preferredSkills} empty="No preferred skills are stated." />
        </Card>
        <Card id="education" title="Education">
          <RequirementList items={groups.education} empty="No education requirements are stated." />
        </Card>
        <Card id="experience" title="Experience">
          <RequirementList
            items={groups.experience}
            empty="No experience requirements are stated."
          />
        </Card>
        <Card id="responsibilities" title="Responsibilities">
          <RequirementList
            items={groups.responsibilities}
            empty="No responsibilities are listed."
          />
        </Card>
        <Card id="eligibility" title="Eligibility">
          <RequirementList
            items={groups.eligibility}
            empty="No eligibility conditions are stated."
          />
        </Card>
        {groups.certifications.length > 0 ? (
          <Card id="certifications" title="Certifications">
            <RequirementList items={groups.certifications} empty="" />
          </Card>
        ) : null}
        {groups.mentionedTechnologies.length > 0 || groups.other.length > 0 ? (
          <Card
            id="informational"
            title="Also mentioned"
            description="Described in the posting, not stated as requirements."
          >
            <SkillList items={[...groups.mentionedTechnologies, ...groups.other]} empty="" />
          </Card>
        ) : null}
      </div>

      {job.description ? (
        <details className="rounded-xl border border-zinc-200 bg-white p-5 text-sm dark:border-zinc-800 dark:bg-zinc-950">
          <summary className="cursor-pointer font-medium">Original job description</summary>
          <pre className="mt-3 font-sans whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">
            {job.description}
          </pre>
        </details>
      ) : null}
    </div>
  );
}
