"use client";

import { useId, useState, type FormEvent } from "react";

import { Button, Field, FormErrors, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  analyzeJob,
  createJob,
  type Importance,
  type Job,
  type ManualRequirement,
  type RequirementType,
} from "@/lib/api/jobs";
import { IMPORTANCE_OPTIONS, REQUIREMENT_TYPE_OPTIONS } from "@/lib/jobs/labels";
import { JOB_TYPE_OPTIONS, WORK_MODE_OPTIONS } from "@/lib/profile/options";

type Mode = "paste" | "manual";
const blank = (value: string) => (value.trim() === "" ? null : value.trim());
const emptyRequirement = (): ManualRequirement => ({
  requirement_type: "skill",
  importance: "required",
  description: "",
});

function useSubmit() {
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setFieldErrors({});
    setFormErrors([]);
    try {
      await action();
    } catch (e) {
      if (e instanceof ApiError) {
        setFieldErrors(e.fieldErrors);
        setFormErrors(e.formErrors);
      } else setFormErrors(["Something went wrong. Please try again."]);
    } finally {
      setBusy(false);
    }
  }
  return { fieldErrors, formErrors, busy, run };
}

function PasteForm({ onCreated }: { onCreated: (job: Job) => void }) {
  const id = useId();
  const [values, setValues] = useState({
    description: "",
    source_url: "",
    title: "",
    company_name: "",
    location: "",
  });
  const { fieldErrors, formErrors, busy, run } = useSubmit();
  const needsDetails = Boolean(fieldErrors.title || fieldErrors.company_name);
  const [showDetails, setShowDetails] = useState(false);
  const set = (key: keyof typeof values) => (value: string) =>
    setValues((v) => ({ ...v, [key]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    await run(async () =>
      onCreated(
        await analyzeJob({
          description: values.description,
          source_url: blank(values.source_url),
          title: blank(values.title),
          company_name: blank(values.company_name),
          location: blank(values.location),
        }),
      ),
    );
  }

  const tooShort = values.description.trim().length < 50;
  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <FormErrors errors={formErrors} />
      <Field
        id={`${id}-description`}
        label="Job description"
        required
        error={fieldErrors.description}
        hint="Paste the full posting: responsibilities, requirements, salary, and so on."
      >
        <textarea
          id={`${id}-description`}
          rows={12}
          className={inputClass}
          value={values.description}
          onChange={(e) => set("description")(e.target.value)}
        />
      </Field>
      <Field
        id={`${id}-url`}
        label="Job posting URL (optional)"
        error={fieldErrors.source_url}
        hint="Saved for your reference only. CareerPilot doesn't fetch job pages, so paste the description above."
      >
        <input
          id={`${id}-url`}
          className={inputClass}
          inputMode="url"
          value={values.source_url}
          onChange={(e) => set("source_url")(e.target.value)}
        />
      </Field>
      {needsDetails || showDetails ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field
            id={`${id}-title`}
            label="Job title"
            required={Boolean(fieldErrors.title)}
            error={fieldErrors.title}
          >
            <input
              id={`${id}-title`}
              className={inputClass}
              value={values.title}
              aria-invalid={fieldErrors.title ? true : undefined}
              onChange={(e) => set("title")(e.target.value)}
            />
          </Field>
          <Field
            id={`${id}-company`}
            label="Company"
            required={Boolean(fieldErrors.company_name)}
            error={fieldErrors.company_name}
          >
            <input
              id={`${id}-company`}
              className={inputClass}
              value={values.company_name}
              aria-invalid={fieldErrors.company_name ? true : undefined}
              onChange={(e) => set("company_name")(e.target.value)}
            />
          </Field>
          <Field id={`${id}-location`} label="Location" error={fieldErrors.location}>
            <input
              id={`${id}-location`}
              className={inputClass}
              value={values.location}
              onChange={(e) => set("location")(e.target.value)}
            />
          </Field>
        </div>
      ) : (
        <button
          type="button"
          className="text-sm text-zinc-600 underline underline-offset-2 dark:text-zinc-400"
          onClick={() => setShowDetails(true)}
        >
          Correct the title, company, or location
        </button>
      )}
      <div className="flex justify-end">
        <Button type="submit" variant="primary" disabled={busy || tooShort}>
          {busy ? "Analyzing…" : "Analyze job"}
        </Button>
      </div>
    </form>
  );
}

function ManualForm({ onCreated }: { onCreated: (job: Job) => void }) {
  const id = useId();
  const [values, setValues] = useState({
    title: "",
    company_name: "",
    location: "",
    workplace_type: "",
    employment_type: "",
    application_deadline: "",
    source_url: "",
    description: "",
  });
  const [requirements, setRequirements] = useState<ManualRequirement[]>([emptyRequirement()]);
  const { fieldErrors, formErrors, busy, run } = useSubmit();
  const set = (key: keyof typeof values) => (value: string) =>
    setValues((v) => ({ ...v, [key]: value }));
  const updateRequirement = (index: number, patch: Partial<ManualRequirement>) =>
    setRequirements((rs) => rs.map((r, i) => (i === index ? { ...r, ...patch } : r)));

  async function submit(event: FormEvent) {
    event.preventDefault();
    await run(async () =>
      onCreated(
        await createJob({
          title: values.title.trim(),
          company_name: values.company_name.trim(),
          location: blank(values.location),
          workplace_type: blank(values.workplace_type),
          employment_type: blank(values.employment_type),
          application_deadline: blank(values.application_deadline),
          source_url: blank(values.source_url),
          description: blank(values.description),
          requirements: requirements.filter((r) => r.description.trim() !== ""),
        }),
      ),
    );
  }

  const select = (
    key: "workplace_type" | "employment_type",
    label: string,
    options: typeof WORK_MODE_OPTIONS,
  ) => (
    <Field id={`${id}-${key}`} label={label} error={fieldErrors[key]}>
      <select
        id={`${id}-${key}`}
        className={inputClass}
        value={values[key]}
        onChange={(e) => set(key)(e.target.value)}
      >
        <option value="">—</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Field>
  );

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <FormErrors errors={formErrors} />
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id={`${id}-title`} label="Job title" required error={fieldErrors.title}>
          <input
            id={`${id}-title`}
            className={inputClass}
            value={values.title}
            onChange={(e) => set("title")(e.target.value)}
          />
        </Field>
        <Field id={`${id}-company`} label="Company" required error={fieldErrors.company_name}>
          <input
            id={`${id}-company`}
            className={inputClass}
            value={values.company_name}
            onChange={(e) => set("company_name")(e.target.value)}
          />
        </Field>
        <Field id={`${id}-location`} label="Location" error={fieldErrors.location}>
          <input
            id={`${id}-location`}
            className={inputClass}
            value={values.location}
            onChange={(e) => set("location")(e.target.value)}
          />
        </Field>
        <Field
          id={`${id}-deadline`}
          label="Application deadline"
          error={fieldErrors.application_deadline}
        >
          <input
            id={`${id}-deadline`}
            type="date"
            className={inputClass}
            value={values.application_deadline}
            onChange={(e) => set("application_deadline")(e.target.value)}
          />
        </Field>
        {select("workplace_type", "Work mode", WORK_MODE_OPTIONS)}
        {select("employment_type", "Employment type", JOB_TYPE_OPTIONS)}
        <Field
          id={`${id}-url`}
          label="Job posting URL"
          error={fieldErrors.source_url}
          className="sm:col-span-2"
        >
          <input
            id={`${id}-url`}
            className={inputClass}
            inputMode="url"
            value={values.source_url}
            onChange={(e) => set("source_url")(e.target.value)}
          />
        </Field>
      </div>

      <fieldset className="space-y-2">
        <legend className="mb-1 text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Requirements
        </legend>
        {requirements.map((r, index) => (
          <div key={index} className="grid gap-2 sm:grid-cols-[9rem_9rem_1fr_auto]">
            <select
              aria-label={`Requirement ${index + 1} type`}
              className={inputClass}
              value={r.requirement_type}
              onChange={(e) =>
                updateRequirement(index, { requirement_type: e.target.value as RequirementType })
              }
            >
              {REQUIREMENT_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <select
              aria-label={`Requirement ${index + 1} importance`}
              className={inputClass}
              value={r.importance}
              onChange={(e) =>
                updateRequirement(index, { importance: e.target.value as Importance })
              }
            >
              {IMPORTANCE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <input
              aria-label={`Requirement ${index + 1}`}
              className={inputClass}
              value={r.description}
              placeholder="e.g. 3+ years of Python"
              maxLength={2000}
              onChange={(e) => updateRequirement(index, { description: e.target.value })}
            />
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Remove requirement ${index + 1}`}
              onClick={() => setRequirements((rs) => rs.filter((_, i) => i !== index))}
            >
              ×
            </Button>
          </div>
        ))}
        <Button size="sm" onClick={() => setRequirements((rs) => [...rs, emptyRequirement()])}>
          + Add requirement
        </Button>
      </fieldset>
      <Field
        id={`${id}-description`}
        label="Description (optional)"
        error={fieldErrors.description}
      >
        <textarea
          id={`${id}-description`}
          rows={4}
          className={inputClass}
          value={values.description}
          onChange={(e) => set("description")(e.target.value)}
        />
      </Field>
      <div className="flex justify-end">
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? "Saving…" : "Save job"}
        </Button>
      </div>
    </form>
  );
}

export function NewJobForm({ onCreated }: { onCreated: (job: Job) => void }) {
  const [mode, setMode] = useState<Mode>("paste");
  return (
    <div>
      <div
        role="tablist"
        aria-label="How to add the job"
        className="mb-4 flex gap-1 rounded-lg bg-zinc-100 p-1 dark:bg-zinc-900"
      >
        {(["paste", "manual"] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={mode === m}
            onClick={() => setMode(m)}
            className="flex-1 rounded-md px-3 py-1.5 text-sm font-medium text-zinc-600 aria-selected:bg-white aria-selected:text-zinc-900 aria-selected:shadow-sm dark:text-zinc-400 dark:aria-selected:bg-zinc-800 dark:aria-selected:text-zinc-100"
          >
            {m === "paste" ? "Paste description" : "Enter manually"}
          </button>
        ))}
      </div>
      {mode === "paste" ? (
        <PasteForm onCreated={onCreated} />
      ) : (
        <ManualForm onCreated={onCreated} />
      )}
    </div>
  );
}
