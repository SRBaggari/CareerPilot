"use client";

import { useId, useState, type FormEvent } from "react";

import { Button, Card, EmptyState, Field, FormErrors, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { updateProfile, type Preferences, type Profile } from "@/lib/api/profile";
import {
  EXPERIENCE_LEVEL_LABELS,
  EXPERIENCE_LEVEL_OPTIONS,
  JOB_TYPE_LABELS,
  JOB_TYPE_OPTIONS,
  label,
  WORK_MODE_LABELS,
  WORK_MODE_OPTIONS,
  type Option,
} from "@/lib/profile/options";

import { TagInput } from "./TagInput";

const pick = (p: Profile): Preferences => ({
  preferred_roles: p.preferred_roles,
  preferred_locations: p.preferred_locations,
  work_modes: p.work_modes,
  job_types: p.job_types,
  experience_level: p.experience_level,
});

function CheckboxGroup({
  legend,
  options,
  values,
  onChange,
  error,
}: {
  legend: string;
  options: Option[];
  values: string[];
  onChange: (v: string[]) => void;
  error?: string;
}) {
  return (
    <fieldset>
      <legend className="mb-1 text-xs font-medium text-zinc-700 dark:text-zinc-300">
        {legend}
      </legend>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {options.map((o) => (
          <label key={o.value} className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-zinc-300"
              checked={values.includes(o.value)}
              onChange={(e) =>
                onChange(
                  e.target.checked ? [...values, o.value] : values.filter((v) => v !== o.value),
                )
              }
            />
            {o.label}
          </label>
        ))}
      </div>
      {error ? (
        <p role="alert" className="mt-1 text-xs text-red-600">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

function Tags({ label: title, values }: { label: string; values: string[] }) {
  return (
    <div>
      <dt className="text-xs text-zinc-500">{title}</dt>
      <dd className="mt-1 flex flex-wrap gap-1.5">
        {values.length > 0 ? (
          values.map((v) => (
            <span
              key={v}
              className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-sm text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200"
            >
              {v}
            </span>
          ))
        ) : (
          <span className="text-sm text-zinc-400">Not set</span>
        )}
      </dd>
    </div>
  );
}

export function PreferencesCard({
  profile,
  onChanged,
}: {
  profile: Profile;
  onChanged: () => Promise<void>;
}) {
  const idPrefix = useId();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Preferences>(() => pick(profile));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const current = pick(profile);
  const isEmpty =
    !current.experience_level &&
    [
      current.preferred_roles,
      current.preferred_locations,
      current.work_modes,
      current.job_types,
    ].every((list) => list.length === 0);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setErrors({});
    setFormErrors([]);
    try {
      await updateProfile(draft);
      setEditing(false);
      await onChanged();
    } catch (e) {
      if (e instanceof ApiError) {
        setErrors(e.fieldErrors);
        setFormErrors(e.formErrors);
      } else setFormErrors(["Something went wrong."]);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card
      id="preferences"
      title="Job preferences"
      description="What you are looking for. Used to find and rank jobs."
      actions={
        editing ? null : (
          <Button
            size="sm"
            onClick={() => {
              setDraft(current);
              setEditing(true);
            }}
          >
            Edit
          </Button>
        )
      }
    >
      {editing ? (
        <form onSubmit={handleSubmit} noValidate className="space-y-4">
          <FormErrors errors={formErrors} />
          <Field
            id={`${idPrefix}-roles`}
            label="Preferred roles"
            error={errors.preferred_roles}
            hint="Press Enter to add each role."
          >
            <TagInput
              id={`${idPrefix}-roles`}
              values={draft.preferred_roles}
              placeholder="e.g. ML Engineer"
              onChange={(v) => setDraft({ ...draft, preferred_roles: v })}
            />
          </Field>
          <Field
            id={`${idPrefix}-locations`}
            label="Preferred locations"
            error={errors.preferred_locations}
            hint="Cities, countries, or “Remote”."
          >
            <TagInput
              id={`${idPrefix}-locations`}
              values={draft.preferred_locations}
              placeholder="e.g. Bengaluru"
              onChange={(v) => setDraft({ ...draft, preferred_locations: v })}
            />
          </Field>
          <CheckboxGroup
            legend="Work mode"
            options={WORK_MODE_OPTIONS}
            values={draft.work_modes}
            error={errors.work_modes}
            onChange={(v) => setDraft({ ...draft, work_modes: v })}
          />
          <CheckboxGroup
            legend="Job type"
            options={JOB_TYPE_OPTIONS}
            values={draft.job_types}
            error={errors.job_types}
            onChange={(v) => setDraft({ ...draft, job_types: v })}
          />
          <Field id={`${idPrefix}-level`} label="Experience level" error={errors.experience_level}>
            <select
              id={`${idPrefix}-level`}
              className={`${inputClass} sm:w-60`}
              value={draft.experience_level ?? ""}
              onChange={(e) => setDraft({ ...draft, experience_level: e.target.value || null })}
            >
              <option value="">—</option>
              {EXPERIENCE_LEVEL_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setEditing(false)} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      ) : isEmpty ? (
        <EmptyState>No preferences set yet.</EmptyState>
      ) : (
        <dl className="grid gap-4 sm:grid-cols-2">
          <Tags label="Preferred roles" values={current.preferred_roles} />
          <Tags label="Preferred locations" values={current.preferred_locations} />
          <Tags
            label="Work mode"
            values={current.work_modes.map((v) => label(WORK_MODE_LABELS, v))}
          />
          <Tags label="Job type" values={current.job_types.map((v) => label(JOB_TYPE_LABELS, v))} />
          <Tags
            label="Experience level"
            values={
              current.experience_level
                ? [label(EXPERIENCE_LEVEL_LABELS, current.experience_level)]
                : []
            }
          />
        </dl>
      )}
    </Card>
  );
}
