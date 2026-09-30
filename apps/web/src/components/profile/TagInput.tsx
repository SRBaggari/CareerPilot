"use client";

import { useState, type KeyboardEvent } from "react";

import { inputClass } from "@/components/ui";

type Props = {
  id: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  max?: number;
};

/** Free-text list input: press Enter (or comma) to add, click × to remove. */
export function TagInput({ id, values, onChange, placeholder, max = 20 }: Props) {
  const [draft, setDraft] = useState("");

  function commit() {
    const value = draft.trim();
    if (!value) return;
    if (!values.some((v) => v.toLowerCase() === value.toLowerCase()) && values.length < max) {
      onChange([...values, value]);
    }
    setDraft("");
  }

  function handleKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && !draft && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  }

  return (
    <div>
      {values.length > 0 ? (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {values.map((value) => (
            <li
              key={value}
              className="inline-flex items-center gap-1 rounded-full bg-zinc-100 py-0.5 pr-1 pl-2.5 text-sm text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200"
            >
              {value}
              <button
                type="button"
                aria-label={`Remove ${value}`}
                onClick={() => onChange(values.filter((v) => v !== value))}
                className="rounded-full px-1 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 dark:hover:bg-zinc-700"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <input
        id={id}
        className={inputClass}
        value={draft}
        placeholder={placeholder}
        maxLength={200}
        disabled={values.length >= max}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKey}
        onBlur={commit}
      />
    </div>
  );
}
