"use client";

import { useEffect, useId, useState } from "react";

import { Badge, Button, Field, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  acceptSuggestion,
  listSuggestions,
  rejectSuggestion,
  type SectionItem,
  type SectionKey,
  type Suggestion,
} from "@/lib/api/profile";
import { valuesFromItem, type FieldDef } from "@/lib/profile/form";
import { label, SKILL_CATEGORY_LABELS, SKILL_CATEGORY_OPTIONS } from "@/lib/profile/options";
import { SECTIONS, type SectionDef } from "@/lib/profile/sections";

import { ItemForm } from "./ItemForm";
import { PERSONAL_FIELDS } from "./PersonalInfoCard";

/** Suggestion section -> profile section definition (list-style sections). */
const SECTION_BY_SUGGESTION: Record<string, SectionKey> = {
  education: "educations",
  work_experience: "work_experiences",
  project: "projects",
  certification: "certifications",
  achievement: "achievements",
  coursework: "coursework",
};
const GROUPS: { section: string; title: string }[] = [
  { section: "personal_info", title: "Personal information" },
  { section: "work_experience", title: "Work experience" },
  { section: "project", title: "Projects" },
  { section: "education", title: "Education" },
  { section: "certification", title: "Certifications" },
  { section: "achievement", title: "Achievements" },
  { section: "coursework", title: "Coursework" },
  { section: "skill", title: "Skills" },
  { section: "evidence", title: "Highlights" },
];
const SKILL_FIELDS: FieldDef[] = [
  { name: "name", label: "Skill", type: "text", required: true, maxLength: 100 },
  { name: "category", label: "Category", type: "select", options: SKILL_CATEGORY_OPTIONS },
];

const sectionDef = (s: Suggestion): SectionDef | undefined =>
  SECTIONS.find((d) => d.key === SECTION_BY_SUGGESTION[s.section]);

function fieldsFor(s: Suggestion): FieldDef[] {
  if (s.section === "skill") return SKILL_FIELDS;
  if (s.section === "personal_info")
    return PERSONAL_FIELDS.filter((f) => f.name in s.proposed_data);
  return sectionDef(s)?.fields ?? [];
}

const highlightsOf = (s: Suggestion): string[] =>
  Array.isArray(s.proposed_data.highlights) ? (s.proposed_data.highlights as string[]) : [];

function Summary({ s }: { s: Suggestion }) {
  const data = s.proposed_data;
  if (s.section === "skill") {
    return (
      <p className="font-medium text-zinc-900 dark:text-zinc-100">
        {String(data.name)}
        {data.category ? (
          <span className="ml-2 text-xs font-normal text-zinc-500">
            {label(SKILL_CATEGORY_LABELS, data.category)}
          </span>
        ) : null}
      </p>
    );
  }
  const def = sectionDef(s);
  if (def) {
    const summary = def.summarize({
      id: s.id,
      sort_order: 0,
      evidence: [],
      ...data,
    } as SectionItem);
    return (
      <div>
        <p className="font-medium text-zinc-900 dark:text-zinc-100">{summary.title}</p>
        {summary.subtitle ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">{summary.subtitle}</p>
        ) : null}
        {summary.meta ? <p className="text-xs text-zinc-500">{summary.meta}</p> : null}
        {typeof data.description === "string" ? (
          <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">{data.description}</p>
        ) : null}
      </div>
    );
  }
  return (
    <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
      {Object.entries(data).map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-zinc-500">
            {PERSONAL_FIELDS.find((f) => f.name === key)?.label ?? key}
          </dt>
          <dd className="break-words text-zinc-800 dark:text-zinc-200">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function EditForm({
  s,
  onSave,
  onCancel,
}: {
  s: Suggestion;
  onSave: (data: Record<string, unknown>) => Promise<void>;
  onCancel: () => void;
}) {
  const id = useId();
  const fields = fieldsFor(s);
  const hasHighlights = s.action === "create" && sectionDef(s) !== undefined;
  const [highlights, setHighlights] = useState(highlightsOf(s).join("\n"));

  return (
    <div className="space-y-3">
      {hasHighlights ? (
        <Field
          id={`${id}-highlights`}
          label="Highlights (one per line)"
          hint="Each line becomes a separate piece of evidence. Remove anything that isn't accurate."
        >
          <textarea
            id={`${id}-highlights`}
            rows={4}
            className={inputClass}
            value={highlights}
            onChange={(e) => setHighlights(e.target.value)}
          />
        </Field>
      ) : null}
      <ItemForm
        fields={fields}
        initial={valuesFromItem(fields, s.proposed_data)}
        submitLabel="Save & accept"
        onCancel={onCancel}
        onSubmit={async (payload) => {
          let data: Record<string, unknown> = payload;
          if (s.section === "personal_info" || s.section === "skill") {
            // Blank means "don't set", never "clear my existing value".
            data = Object.fromEntries(Object.entries(payload).filter(([, v]) => v !== null));
          }
          if (hasHighlights) {
            data = {
              ...data,
              highlights: highlights
                .split("\n")
                .map((h) => h.trim())
                .filter(Boolean),
            };
          }
          await onSave(data);
        }}
      />
    </div>
  );
}

/**
 * EXTRACTED INFORMATION — produced automatically (e.g. from a resume) and therefore never
 * trusted: nothing here is part of the master profile until the candidate accepts it.
 */
export function ExtractedInfoReview({
  count,
  onChanged,
}: {
  count: number;
  onChanged: () => Promise<void>;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<{ id: string | null; message: string } | null>(null);

  useEffect(() => {
    if (count === 0) return;
    let cancelled = false;
    listSuggestions().then(
      (items) => !cancelled && setSuggestions(items),
      (e: unknown) =>
        !cancelled &&
        setError({
          id: null,
          message: e instanceof ApiError ? e.message : "Could not load extracted information.",
        }),
    );
    return () => {
      cancelled = true;
    };
  }, [count]);

  if (count === 0) return null;

  async function run(s: Suggestion, action: () => Promise<unknown>) {
    setBusyId(s.id);
    setError(null);
    try {
      await action();
      setEditing(null);
      await onChanged();
    } catch (e) {
      const message =
        e instanceof ApiError
          ? [...e.formErrors, ...Object.entries(e.fieldErrors).map(([f, m]) => `${f}: ${m}`)].join(
              " ",
            ) || e.message
          : "Something went wrong.";
      setError({ id: s.id, message });
      throw e;
    } finally {
      setBusyId(null);
    }
  }

  const grouped = GROUPS.map((g) => ({
    ...g,
    items: suggestions.filter((s) => s.section === g.section),
  })).filter((g) => g.items.length > 0);

  return (
    <section
      id="extracted"
      aria-labelledby="extracted-heading"
      className="rounded-xl border-2 border-dashed border-amber-300 bg-amber-50/60 p-5 dark:border-amber-800 dark:bg-amber-950/20"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2
          id="extracted-heading"
          className="text-base font-semibold text-amber-950 dark:text-amber-100"
        >
          Extracted information
        </h2>
        <Badge tone="ai">{count} awaiting review</Badge>
      </div>
      <p className="mt-1 text-sm text-amber-900/80 dark:text-amber-200/80">
        This was read automatically and <strong>is not part of your profile</strong> until you
        accept it. Check each item against your resume. Edit anything that&apos;s off and reject
        anything that isn&apos;t true.
      </p>
      {error && error.id === null ? (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error.message}
        </p>
      ) : null}

      <div className="mt-4 space-y-5">
        {grouped.map((group) => (
          <div key={group.section}>
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-amber-900 uppercase dark:text-amber-200">
              {group.title} ({group.items.length})
            </h3>
            <ul className="space-y-2">
              {group.items.map((s) => (
                <li
                  key={s.id}
                  aria-label={`Extracted ${group.title}`}
                  className="rounded-lg border border-amber-200 bg-white p-4 dark:border-amber-900 dark:bg-zinc-950"
                >
                  {editing === s.id ? (
                    <EditForm
                      s={s}
                      onCancel={() => setEditing(null)}
                      onSave={(data) => run(s, () => acceptSuggestion(s.id, data))}
                    />
                  ) : (
                    <>
                      <Summary s={s} />
                      {highlightsOf(s).length > 0 ? (
                        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-zinc-700 dark:text-zinc-300">
                          {highlightsOf(s).map((h) => (
                            <li key={h}>{h}</li>
                          ))}
                        </ul>
                      ) : null}
                      {s.source_excerpt ? (
                        <details className="mt-2 text-xs text-zinc-500">
                          <summary className="cursor-pointer">Show text from resume</summary>
                          <pre className="mt-1 rounded bg-zinc-50 p-2 whitespace-pre-wrap dark:bg-zinc-900">
                            {s.source_excerpt}
                          </pre>
                        </details>
                      ) : null}
                      <div className="mt-3 flex gap-2">
                        <Button
                          size="sm"
                          variant="primary"
                          disabled={busyId === s.id}
                          onClick={() =>
                            void run(s, () => acceptSuggestion(s.id)).catch(() => undefined)
                          }
                        >
                          Accept
                        </Button>
                        <Button
                          size="sm"
                          disabled={busyId === s.id}
                          onClick={() => setEditing(s.id)}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={busyId === s.id}
                          onClick={() =>
                            void run(s, () => rejectSuggestion(s.id)).catch(() => undefined)
                          }
                        >
                          Reject
                        </Button>
                      </div>
                    </>
                  )}
                  {error?.id === s.id ? (
                    <p role="alert" className="mt-2 text-sm text-red-700">
                      {error.message}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
