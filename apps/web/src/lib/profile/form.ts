/**
 * Generic form definitions and value conversion for profile section items.
 * Validation here is a convenience for fast feedback; the API is authoritative.
 */
import type { Option } from "./options";

export type FieldType =
  "text" | "textarea" | "url" | "email" | "date" | "number" | "select" | "checkbox" | "education";

export type FieldDef = {
  name: string;
  label: string;
  type: FieldType;
  required?: boolean;
  maxLength?: number;
  options?: Option[];
  placeholder?: string;
  step?: string;
  wide?: boolean;
};

export type FormValues = Record<string, string | boolean>;

export function emptyValues(fields: FieldDef[]): FormValues {
  return Object.fromEntries(fields.map((f) => [f.name, f.type === "checkbox" ? false : ""]));
}

export function valuesFromItem(fields: FieldDef[], item: Record<string, unknown>): FormValues {
  return Object.fromEntries(
    fields.map((f) => {
      const value = item[f.name];
      if (f.type === "checkbox") return [f.name, Boolean(value)];
      return [f.name, value === null || value === undefined ? "" : String(value)];
    }),
  );
}

/** Convert form values to an API payload: blank strings become null. */
export function toPayload(fields: FieldDef[], values: FormValues): Record<string, unknown> {
  return Object.fromEntries(
    fields.map((f) => {
      const value = values[f.name];
      if (f.type === "checkbox") return [f.name, Boolean(value)];
      const text = typeof value === "string" ? value.trim() : "";
      return [f.name, text === "" ? null : text];
    }),
  );
}

export function validate(fields: FieldDef[], values: FormValues): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const f of fields) {
    const value = values[f.name];
    const text = typeof value === "string" ? value.trim() : "";
    if (f.required && text === "") errors[f.name] = `${f.label} is required`;
    else if (f.maxLength && text.length > f.maxLength)
      errors[f.name] = `${f.label} must be at most ${f.maxLength} characters`;
  }
  return errors;
}
