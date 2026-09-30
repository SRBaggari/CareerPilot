import type { ReactNode } from "react";

import type { Claim, ResumeContent, TailoredResume } from "@/lib/api/tailoredResumes";

const MONTH = new Intl.DateTimeFormat("en-GB", {
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

export const monthYear = (value: string | null) => (value ? MONTH.format(new Date(value)) : "");

export function dateRange(start: string | null, end: string | null, current = false) {
  const finish = current ? "Present" : monthYear(end);
  const begin = monthYear(start);
  return begin && finish ? `${begin} – ${finish}` : begin || finish;
}

type Evidence = TailoredResume["evidence"];

/** A generated statement; with sources shown, the evidence it rests on follows it. */
function ClaimText({
  claim,
  evidence,
  showSources,
}: {
  claim: Claim;
  evidence: Evidence;
  showSources: boolean;
}) {
  const sources = claim.evidence_ids.map((id) => evidence[id]).filter(Boolean);
  return (
    <>
      {claim.text}
      {showSources && sources.length > 0 ? (
        <span className="mt-1 block space-y-0.5">
          {sources.map((source) => (
            <span
              key={source.content}
              className="block border-l-2 border-sky-300 pl-2 text-xs text-sky-900 dark:border-sky-700 dark:text-sky-300"
            >
              Evidence{source.record_label ? ` (${source.record_label})` : ""}: “{source.content}”
            </span>
          ))}
        </span>
      ) : null}
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-5">
      <h3 className="border-b border-zinc-300 pb-0.5 text-xs font-bold tracking-wider text-zinc-800 uppercase dark:border-zinc-700 dark:text-zinc-200">
        {title}
      </h3>
      <div className="mt-2 space-y-3">{children}</div>
    </section>
  );
}

function Entry({
  title,
  right,
  children,
}: {
  title: string;
  right?: string;
  children?: ReactNode;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <p className="font-semibold text-zinc-900 dark:text-zinc-100">{title}</p>
        {right ? <p className="text-sm text-zinc-600 dark:text-zinc-400">{right}</p> : null}
      </div>
      {children}
    </div>
  );
}

function Bullets({
  claims,
  evidence,
  showSources,
}: {
  claims: Claim[];
  evidence: Evidence;
  showSources: boolean;
}) {
  if (claims.length === 0) return null;
  return (
    <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-zinc-800 dark:text-zinc-200">
      {claims.map((claim) => (
        <li key={claim.claim_id ?? claim.text}>
          <ClaimText claim={claim} evidence={evidence} showSources={showSources} />
        </li>
      ))}
    </ul>
  );
}

/** The resume as it will be downloaded: record facts plus verified claims only. */
export function ResumePreview({
  content,
  evidence,
  showSources,
}: {
  content: ResumeContent;
  evidence: Evidence;
  showSources: boolean;
}) {
  const h = content.header;
  const contact = [
    h.contact_email,
    h.phone,
    h.location,
    h.linkedin_url,
    h.github_url,
    h.website_url,
  ]
    .filter(Boolean)
    .join(" | ");
  return (
    <article
      aria-label="Resume preview"
      className="rounded-xl border border-zinc-200 bg-white p-8 shadow-sm dark:border-zinc-800 dark:bg-zinc-950"
    >
      <h2 className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">{h.full_name}</h2>
      {h.headline ? <p className="text-zinc-600 italic dark:text-zinc-400">{h.headline}</p> : null}
      {contact ? <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{contact}</p> : null}

      {content.summary.length > 0 ? (
        <Section title="Summary">
          <p className="text-sm text-zinc-800 dark:text-zinc-200">
            {content.summary.map((claim, i) => (
              <span key={claim.claim_id ?? i}>
                {i > 0 ? " " : ""}
                <ClaimText claim={claim} evidence={evidence} showSources={showSources} />
              </span>
            ))}
          </p>
        </Section>
      ) : null}
      {content.skills.length > 0 ? (
        <Section title="Skills">
          <p className="text-sm text-zinc-800 dark:text-zinc-200">
            {content.skills.map((s) => s.text).join(", ")}
          </p>
        </Section>
      ) : null}
      {content.experience.length > 0 ? (
        <Section title="Experience">
          {content.experience.map((job) => (
            <Entry
              key={job.record_id}
              title={`${job.title}, ${job.company_name}`}
              right={dateRange(job.start_date, job.end_date, job.is_current)}
            >
              {job.location ? <p className="text-sm text-zinc-500 italic">{job.location}</p> : null}
              <Bullets claims={job.bullets} evidence={evidence} showSources={showSources} />
            </Entry>
          ))}
        </Section>
      ) : null}
      {content.projects.length > 0 ? (
        <Section title="Projects">
          {content.projects.map((project) => (
            <Entry
              key={project.record_id}
              title={project.role ? `${project.title} (${project.role})` : project.title}
              right={dateRange(project.start_date, project.end_date)}
            >
              {project.url ? <p className="text-sm text-zinc-500 italic">{project.url}</p> : null}
              <Bullets claims={project.bullets} evidence={evidence} showSources={showSources} />
            </Entry>
          ))}
        </Section>
      ) : null}
      {content.education.length > 0 ? (
        <Section title="Education">
          {content.education.map((school) => {
            const degree = [school.degree, school.field_of_study].filter(Boolean).join(", ");
            return (
              <Entry
                key={school.record_id}
                title={degree ? `${degree} – ${school.institution}` : school.institution}
                right={dateRange(school.start_date, school.end_date)}
              >
                {school.gpa ? (
                  <p className="text-sm text-zinc-500 italic">
                    GPA {Number(school.gpa)}
                    {school.gpa_scale ? `/${Number(school.gpa_scale)}` : ""}
                  </p>
                ) : null}
              </Entry>
            );
          })}
        </Section>
      ) : null}
      {content.coursework.length > 0 ? (
        <Section title="Relevant coursework">
          <p className="text-sm text-zinc-800 dark:text-zinc-200">
            {content.coursework.map((c) => c.course_name).join(", ")}
          </p>
        </Section>
      ) : null}
      {content.certifications.length > 0 ? (
        <Section title="Certifications">
          {content.certifications.map((cert) => (
            <Entry
              key={cert.record_id}
              title={cert.issuer ? `${cert.name}, ${cert.issuer}` : cert.name}
              right={monthYear(cert.issue_date)}
            />
          ))}
        </Section>
      ) : null}
      {content.achievements.length > 0 ? (
        <Section title="Achievements">
          {content.achievements.map((a) => (
            <Entry key={a.record_id} title={a.title} right={monthYear(a.achieved_on)} />
          ))}
        </Section>
      ) : null}
    </article>
  );
}
