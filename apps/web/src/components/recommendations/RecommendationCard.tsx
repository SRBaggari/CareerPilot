"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Button } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { EMPLOYMENT_LABELS, LEVEL_LABELS, WORK_MODE_LABELS } from "@/lib/api/discovery";
import {
  actOn,
  type Recommendation,
  type RecommendationAction,
  type SkillResult,
} from "@/lib/api/recommendations";

const STATUS_TONE: Record<string, string> = {
  matched: "text-emerald-700 dark:text-emerald-400",
  partial: "text-amber-700 dark:text-amber-400",
  missing: "text-red-700 dark:text-red-400",
  unknown: "text-zinc-500",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <h4 className="text-xs font-semibold tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
        {title}
      </h4>
      {children}
    </div>
  );
}

function Skills({ label, skills, tone }: { label: string; skills: SkillResult[]; tone: string }) {
  return (
    <ul aria-label={label} className="flex flex-wrap gap-1 text-xs">
      {skills.map((s) => (
        <li key={s.skill} title={s.explanation} className={`rounded px-1.5 py-0.5 ${tone}`}>
          {s.skill}
          {s.importance === "preferred" ? <span className="opacity-70"> (preferred)</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** One recommended job: what it is, why it is recommended, and what to do next. */
export function RecommendationCard({
  rec,
  onChange,
}: {
  rec: Recommendation;
  onChange: (rec: Recommendation) => void;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<RecommendationAction | "tailor" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [details, setDetails] = useState(false);
  const e = rec.explanation;
  const headingId = `rec-${rec.id}`;

  async function act(
    action: RecommendationAction,
    goTo?: (jobId: string) => string,
    label: RecommendationAction | "tailor" = action,
  ) {
    setBusy(label);
    setError(null);
    try {
      const result = await actOn(rec.id, action);
      onChange(result.recommendation);
      if (goTo && result.job_id) router.push(goTo(result.job_id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <article
      aria-labelledby={headingId}
      className="space-y-4 rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id={headingId} className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            {rec.rank ? <span className="mr-2 text-zinc-400">#{rec.rank}</span> : null}
            {rec.title}
          </h3>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            {rec.company}
            {rec.location ? ` · ${rec.location}` : ""}
          </p>
          <p className="mt-1 flex flex-wrap gap-1.5 text-xs">
            {rec.work_mode ? <Badge>{WORK_MODE_LABELS[rec.work_mode]}</Badge> : null}
            {rec.employment_type ? (
              <Badge>{EMPLOYMENT_LABELS[rec.employment_type] ?? rec.employment_type}</Badge>
            ) : null}
            {rec.experience_level ? <Badge>{LEVEL_LABELS[rec.experience_level]}</Badge> : null}
            {rec.deadline ? <Badge>Apply by {rec.deadline}</Badge> : null}
            {rec.status === "saved" ? <Badge tone="lock">Saved</Badge> : null}
            {rec.application ? (
              <Badge tone="lock">Application: {rec.application.status}</Badge>
            ) : null}
            {rec.is_stale ? <Badge tone="ai">Profile changed: refresh</Badge> : null}
          </p>
        </div>
        <p className="text-right text-sm" aria-label="Required requirements covered">
          <strong className="text-zinc-900 dark:text-zinc-50">
            {e.required_met} of {e.required_total}
          </strong>{" "}
          required met
          {e.required_partial ? (
            <span className="block text-xs">+{e.required_partial} partly</span>
          ) : null}
        </p>
      </header>

      {rec.eligible ? (
        <Section title="Why it's recommended">
          <p className="text-sm text-zinc-800 dark:text-zinc-200">{e.summary}</p>
          <ul
            className="list-disc space-y-0.5 pl-5 text-sm text-zinc-700 dark:text-zinc-300"
            aria-label="Reasons"
          >
            {e.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </Section>
      ) : (
        <Section title="Filtered out">
          <ul
            className="list-disc pl-5 text-sm text-red-800 dark:text-red-300"
            aria-label="Exclusions"
          >
            {rec.exclusions.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </Section>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Section title="Matched skills">
          {e.matched_skills.length ? (
            <Skills
              label="Matched skills"
              skills={e.matched_skills}
              tone="bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200"
            />
          ) : (
            <p className="text-sm text-zinc-500 italic">None yet.</p>
          )}
          {e.partial_skills.length ? (
            <Skills
              label="Partly shown skills"
              skills={e.partial_skills}
              tone="bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
            />
          ) : null}
        </Section>
        <Section title="Missing skills">
          {e.missing_skills.length ? (
            <Skills
              label="Missing skills"
              skills={e.missing_skills}
              tone="bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200"
            />
          ) : (
            <p className="text-sm text-zinc-500 italic">None of the stated skills are missing.</p>
          )}
        </Section>
      </div>

      <Section title="Eligibility concerns">
        {rec.concerns.length ? (
          <ul
            className="list-disc pl-5 text-sm text-amber-800 dark:text-amber-300"
            aria-label="Eligibility concerns"
          >
            {rec.concerns.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-zinc-500 italic">
            None found. Check the posting before applying.
          </p>
        )}
      </Section>

      <Section title="Relevant projects">
        {e.relevant_projects.length ? (
          <ul className="space-y-2" aria-label="Relevant projects">
            {e.relevant_projects.map((p) => (
              <li key={p.title} className="text-sm">
                <span className="font-medium text-zinc-900 dark:text-zinc-100">{p.title}</span>
                <span className="text-zinc-500"> covers {p.supports.join(", ")}</span>
                {p.evidence.map((ev) => (
                  <span
                    key={ev}
                    className="mt-0.5 block border-l-2 border-sky-300 pl-2 text-xs text-sky-900 dark:border-sky-700 dark:text-sky-300"
                  >
                    “{ev}”
                  </span>
                ))}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-zinc-500 italic">
            No project evidence relates to this job yet.
          </p>
        )}
      </Section>

      <div>
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={details}
          onClick={() => setDetails(!details)}
        >
          {details ? "Hide requirement details" : "Show every requirement"}
        </Button>
        {details ? (
          <ul className="mt-2 space-y-1 text-sm" aria-label="Requirement details">
            {e.requirements.map((r) => (
              <li key={r.requirement}>
                <span className={`font-medium ${STATUS_TONE[r.status]}`}>{r.status}</span>{" "}
                <span className="text-xs text-zinc-500">({r.importance})</span> {r.requirement}
                <span className="block text-xs text-zinc-600 dark:text-zinc-400">
                  {r.explanation}
                </span>
              </li>
            ))}
            <li className="pt-1 text-xs text-zinc-500">{e.disclaimer}</li>
          </ul>
        ) : null}
      </div>

      {error ? (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2 border-t border-zinc-100 pt-3 dark:border-zinc-900">
        {rec.status === "saved" ? (
          <Button size="sm" disabled={busy !== null} onClick={() => void act("restore")}>
            Unsave
          </Button>
        ) : rec.status === "ignored" ? null : (
          <Button size="sm" disabled={busy !== null} onClick={() => void act("save")}>
            Save Job
          </Button>
        )}
        {rec.status === "ignored" ? (
          <Button size="sm" disabled={busy !== null} onClick={() => void act("restore")}>
            Restore
          </Button>
        ) : (
          <Button
            size="sm"
            variant="danger"
            disabled={busy !== null}
            onClick={() => void act("ignore")}
          >
            Ignore Job
          </Button>
        )}
        <Button
          size="sm"
          disabled={busy !== null}
          onClick={() => void act("analyze", (id) => `/jobs/${id}/match`)}
        >
          {busy === "analyze" ? "Analyzing…" : "Analyze Job"}
        </Button>
        <Button
          size="sm"
          disabled={busy !== null}
          onClick={() => void act("analyze", (id) => `/jobs/${id}/resume`, "tailor")}
        >
          {busy === "tailor" ? "Preparing…" : "Tailor Resume"}
        </Button>
        {rec.application ? (
          <Link
            href={`/applications/${rec.application.id}`}
            className="inline-flex items-center rounded-md bg-zinc-900 px-2 py-1 text-xs font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
          >
            Open in tracker
          </Link>
        ) : (
          <Button
            size="sm"
            variant="primary"
            disabled={busy !== null}
            onClick={() => void act("start-application")}
          >
            {busy === "start-application" ? "Starting…" : "Start Application"}
          </Button>
        )}
        {rec.url ? (
          <a
            href={rec.url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto self-center text-xs text-zinc-500 underline"
          >
            View original posting
          </a>
        ) : null}
      </div>
      {rec.application ? (
        <p className="text-xs text-zinc-500" role="status">
          You&apos;re tracking this application. Nothing is submitted until you approve it and
          submit it yourself.
        </p>
      ) : null}
    </article>
  );
}
