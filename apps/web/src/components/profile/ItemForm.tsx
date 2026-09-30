"use client";

import { useId, useState, type FormEvent } from "react";

import { Button, Field, FormErrors, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import type { SectionItem } from "@/lib/api/profile";
import { toPayload, validate, type FieldDef, type FormValues } from "@/lib/profile/form";

type Props = {
  fields: FieldDef[];
  initial: FormValues;
  submitLabel: string;
  onSubmit: (payload: Record<string, unknown>) => Promise<void>;
  onCancel?: () => void;
  educations?: SectionItem[];
};

/** Renders a form from field definitions and maps API validation errors onto fields. */
export function ItemForm({
  fields,
  initial,
  submitLabel,
  onSubmit,
  onCancel,
  educations = [],
}: Props) {
  const formId = useId();
  const [values, setValues] = useState<FormValues>(initial);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const set = (name: string, value: string | boolean) =>
    setValues((v) => ({ ...v, [name]: value }));

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const errors = validate(fields, values);
    setFieldErrors(errors);
    setFormErrors([]);
    if (Object.keys(errors).length > 0) return;
    setSaving(true);
    try {
      await onSubmit(toPayload(fields, values));
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        const unmatched = Object.entries(error.fieldErrors)
          .filter(([name]) => !fields.some((f) => f.name === name))
          .map(([, message]) => message);
        setFormErrors([...error.formErrors, ...unmatched]);
      } else {
        setFormErrors(["Something went wrong. Please try again."]);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-4">
      <FormErrors errors={formErrors} />
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((f) => {
          const id = `${formId}-${f.name}`;
          const error = fieldErrors[f.name];
          const common = {
            id,
            "aria-invalid": error ? true : undefined,
            "aria-describedby": error ? `${id}-error` : undefined,
          };
          const value = values[f.name];
          if (f.type === "checkbox") {
            return (
              <label
                key={f.name}
                className="flex items-center gap-2 self-end text-sm sm:col-span-2"
              >
                <input
                  {...common}
                  type="checkbox"
                  checked={Boolean(value)}
                  onChange={(e) => set(f.name, e.target.checked)}
                  className="h-4 w-4 rounded border-zinc-300"
                />
                {f.label}
              </label>
            );
          }
          const text = typeof value === "string" ? value : "";
          let control;
          if (f.type === "textarea") {
            control = (
              <textarea
                {...common}
                rows={3}
                value={text}
                placeholder={f.placeholder}
                onChange={(e) => set(f.name, e.target.value)}
                className={inputClass}
              />
            );
          } else if (f.type === "select" || f.type === "education") {
            const options =
              f.type === "education"
                ? educations.map((e) => ({ value: e.id, label: String(e.institution) }))
                : (f.options ?? []);
            control = (
              <select
                {...common}
                value={text}
                onChange={(e) => set(f.name, e.target.value)}
                className={inputClass}
              >
                <option value="">—</option>
                {options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            );
          } else {
            const inputType = f.type === "url" ? "text" : f.type; // server normalizes bare domains
            control = (
              <input
                {...common}
                type={inputType}
                step={f.step}
                value={text}
                placeholder={f.placeholder}
                inputMode={f.type === "url" ? "url" : undefined}
                onChange={(e) => set(f.name, e.target.value)}
                className={inputClass}
              />
            );
          }
          return (
            <Field
              key={f.name}
              id={id}
              label={f.label}
              required={f.required}
              error={error}
              className={f.wide ? "sm:col-span-2" : ""}
            >
              {control}
            </Field>
          );
        })}
      </div>
      <div className="flex justify-end gap-2">
        {onCancel ? (
          <Button onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        ) : null}
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Saving…" : submitLabel}
        </Button>
      </div>
    </form>
  );
}
