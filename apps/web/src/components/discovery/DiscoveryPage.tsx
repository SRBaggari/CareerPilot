"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState, Field, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  EMPLOYMENT_LABELS,
  EMPTY_FILTERS,
  importPosting,
  LEVEL_LABELS,
  searchJobs,
  WORK_MODE_LABELS,
  type DiscoveredJob,
  type DiscoveryFilters,
  type DiscoveryResults,
  type EmploymentType,
  type ExperienceLevel,
} from "@/lib/api/discovery";

const PAGE_SIZE = 10;
const EMPLOYMENT_OPTIONS: EmploymentType[] = ["internship", "full_time", "part_time", "contract"];
const LEVEL_OPTIONS = Object.keys(LEVEL_LABELS) as ExperienceLevel[];

function toggle<T>(items: T[], item: T): T[] {
  return items.includes(item) ? items.filter((i) => i !== item) : [...items, item];
}

function JobResult({
  job,
  onImported,
}: {
  job: DiscoveredJob;
  onImported: (job: DiscoveredJob, jobId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const headingId = `posting-${job.source}-${job.source_identifier}`;

  async function importIt() {
    setBusy(true);
    setError(null);
    try {
      const result = await importPosting(job.source, job.source_identifier);
      onImported(job, result.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Import failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      aria-labelledby={headingId}
      className="space-y-2 rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id={headingId} className="font-medium text-zinc-900 dark:text-zinc-50">
            {job.title}
          </h3>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            {job.company}
            {job.location ? ` · ${job.location}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {job.imported_job_id ? (
            <>
              <Link
                href={`/jobs/${job.imported_job_id}`}
                className="inline-flex items-center rounded-md border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-800 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-100 dark:hover:bg-zinc-800"
              >
                Open job
              </Link>
              <Link
                href={`/jobs/${job.imported_job_id}/match`}
                className="inline-flex items-center rounded-md bg-zinc-900 px-2 py-1 text-xs font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
              >
                Compare with my profile
              </Link>
            </>
          ) : (
            <Button size="sm" variant="primary" onClick={() => void importIt()} disabled={busy}>
              {busy ? "Importing…" : "Import & analyze"}
            </Button>
          )}
        </div>
      </div>
      <p className="flex flex-wrap gap-1.5 text-xs">
        {job.work_mode ? <Badge>{WORK_MODE_LABELS[job.work_mode]}</Badge> : null}
        {job.employment_type ? (
          <Badge>{EMPLOYMENT_LABELS[job.employment_type] ?? job.employment_type}</Badge>
        ) : null}
        {job.experience_level ? <Badge>{LEVEL_LABELS[job.experience_level]}</Badge> : null}
        {job.imported_job_id ? <Badge tone="lock">Imported</Badge> : null}
      </p>
      {job.skills.length > 0 ? (
        <p className="flex flex-wrap gap-1 text-xs" aria-label="Skills in this posting">
          {job.skills.map((skill) => (
            <span
              key={skill}
              className={`rounded px-1.5 py-0.5 ${
                job.matched_skills.includes(skill)
                  ? "bg-emerald-100 font-medium text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200"
                  : "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
              }`}
            >
              {skill}
            </span>
          ))}
        </p>
      ) : null}
      <p className="text-xs text-zinc-500">
        {job.posted_date ? `Posted ${job.posted_date}` : "Posting date not given"}
        {job.deadline ? ` · Apply by ${job.deadline}` : ""} · Source: {job.source}
        {job.url ? (
          <>
            {" · "}
            <a
              href={job.url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-zinc-700"
            >
              View original posting
            </a>
          </>
        ) : null}
      </p>
      {error ? (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      ) : null}
      <Button size="sm" variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Hide description" : "Show description"}
      </Button>
      {open ? (
        <p className="text-sm whitespace-pre-line text-zinc-700 dark:text-zinc-300">
          {job.description}
        </p>
      ) : null}
    </li>
  );
}

export function DiscoveryPage() {
  const [filters, setFilters] = useState<DiscoveryFilters>(EMPTY_FILTERS);
  const [skillsText, setSkillsText] = useState("");
  const [results, setResults] = useState<DiscoveryResults | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (applied: DiscoveryFilters, toPage: number) => {
    setBusy(true);
    setError(null);
    try {
      setResults(await searchJobs(applied, toPage, PAGE_SIZE));
      setPage(toPage);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Search failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    searchJobs(EMPTY_FILTERS, 1, PAGE_SIZE).then(
      (found) => !cancelled && setResults(found),
      (e: unknown) =>
        !cancelled &&
        setError(e instanceof ApiError ? e.message : "Search failed. Please try again."),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const applied = (): DiscoveryFilters => ({
    ...filters,
    skills: skillsText
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  });
  const pages = results ? Math.max(1, Math.ceil(results.total / results.page_size)) : 1;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Discover jobs
        </h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Search job sources that permit automated access. Import a posting to analyze it, match it
          against your profile, and tailor documents for it.
        </p>
      </div>

      <Card title="Filters">
        <form
          aria-label="Job filters"
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run(applied(), 1);
          }}
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <Field id="role" label="Role">
              <input
                id="role"
                className={inputClass}
                value={filters.role}
                placeholder="e.g. ML engineer"
                onChange={(e) => setFilters({ ...filters, role: e.target.value })}
              />
            </Field>
            <Field id="location" label="Location">
              <input
                id="location"
                className={inputClass}
                value={filters.location}
                placeholder="e.g. Hyderabad"
                onChange={(e) => setFilters({ ...filters, location: e.target.value })}
              />
            </Field>
            <Field id="remote" label="Remote">
              <select
                id="remote"
                className={inputClass}
                value={filters.remote}
                onChange={(e) =>
                  setFilters({ ...filters, remote: e.target.value as DiscoveryFilters["remote"] })
                }
              >
                <option value="">Any</option>
                <option value="true">Remote only</option>
                <option value="false">Not remote</option>
              </select>
            </Field>
          </div>
          <Field
            id="skills"
            label="Skills"
            hint="Comma-separated; jobs naming any of them are shown, best matches first."
          >
            <input
              id="skills"
              className={inputClass}
              value={skillsText}
              placeholder="e.g. Python, Docker"
              onChange={(e) => setSkillsText(e.target.value)}
            />
          </Field>
          <fieldset>
            <legend className="mb-1 text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Job type
            </legend>
            <div className="flex flex-wrap gap-3 text-sm">
              {EMPLOYMENT_OPTIONS.map((type) => (
                <label key={type} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={filters.employmentTypes.includes(type)}
                    onChange={() =>
                      setFilters({
                        ...filters,
                        employmentTypes: toggle(filters.employmentTypes, type),
                      })
                    }
                  />
                  {EMPLOYMENT_LABELS[type]}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend className="mb-1 text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Experience level
            </legend>
            <div className="flex flex-wrap gap-3 text-sm">
              {LEVEL_OPTIONS.map((level) => (
                <label key={level} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={filters.experienceLevels.includes(level)}
                    onChange={() =>
                      setFilters({
                        ...filters,
                        experienceLevels: toggle(filters.experienceLevels, level),
                      })
                    }
                  />
                  {LEVEL_LABELS[level]}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Searching…" : "Search"}
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                setFilters(EMPTY_FILTERS);
                setSkillsText("");
                void run(EMPTY_FILTERS, 1);
              }}
            >
              Clear filters
            </Button>
          </div>
        </form>
      </Card>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      ) : null}
      {results?.errors.length ? (
        <ul
          role="status"
          className="list-disc rounded-lg border border-amber-300 bg-amber-50 p-3 pl-8 text-sm text-amber-900"
        >
          {results.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}

      {results ? (
        <section aria-label="Results" className="space-y-3">
          <p className="text-sm text-zinc-600 dark:text-zinc-400" role="status">
            {results.total} {results.total === 1 ? "job" : "jobs"} found
          </p>
          {results.jobs.length === 0 ? (
            <EmptyState>No jobs match these filters.</EmptyState>
          ) : (
            <ul className="space-y-3">
              {results.jobs.map((job) => (
                <JobResult
                  key={`${job.source}:${job.source_identifier}`}
                  job={job}
                  onImported={(imported, jobId) =>
                    setResults({
                      ...results,
                      jobs: results.jobs.map((j) =>
                        j === imported ? { ...j, imported_job_id: jobId } : j,
                      ),
                    })
                  }
                />
              ))}
            </ul>
          )}
          {pages > 1 ? (
            <nav aria-label="Pages" className="flex items-center gap-2 text-sm">
              <Button
                size="sm"
                disabled={busy || page <= 1}
                onClick={() => void run(applied(), page - 1)}
              >
                Previous
              </Button>
              <span>
                Page {page} of {pages}
              </span>
              <Button
                size="sm"
                disabled={busy || page >= pages}
                onClick={() => void run(applied(), page + 1)}
              >
                Next
              </Button>
            </nav>
          ) : null}
          <p className="text-xs text-zinc-500">
            Sources:{" "}
            {results.sources
              .map(
                (s) =>
                  `${s.display_name}${s.enabled ? "" : ` (unavailable: ${s.reasons.join(" ")})`}`,
              )
              .join("; ")}
            . CareerPilot only uses sources that permit automated access and never bypasses logins,
            CAPTCHAs or robots rules.
          </p>
        </section>
      ) : null}
    </div>
  );
}
