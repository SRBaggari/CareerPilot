"use client";

import { useId, useState, type FormEvent } from "react";

import { Badge, Button, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  addEvidence,
  deleteEvidence,
  updateEvidence,
  type Evidence,
  type EvidenceSourceType,
} from "@/lib/api/profile";

type Props = {
  evidence: Evidence[];
  sourceType: EvidenceSourceType;
  subjectId: string | null;
  hint: string;
  onChanged: () => Promise<void>;
};

const ORIGIN_NOTE: Record<Evidence["origin"], string | null> = {
  user_entered: null,
  resume_extracted: "From your resume · approved by you",
  ai_suggested: "AI suggestion · approved by you",
};

/**
 * Concrete, verifiable statements ("highlights") about one profile item. These are the
 * evidence that future generated resume and cover-letter claims must cite.
 */
export function EvidenceList({ evidence, sourceType, subjectId, hint, onChanged }: Props) {
  const inputId = useId();
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: string; content: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChanged();
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content) return;
    if (await run(() => addEvidence({ source_type: sourceType, subject_id: subjectId, content }))) {
      setDraft("");
    }
  }

  async function handleSave(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const content = editing.content.trim();
    if (content && (await run(() => updateEvidence(editing.id, content)))) setEditing(null);
  }

  return (
    <div className="mt-3">
      <p className="text-xs font-medium tracking-wide text-zinc-500 uppercase">Highlights</p>
      {evidence.length > 0 ? (
        <ul className="mt-1.5 space-y-1.5">
          {evidence.map((ev) =>
            editing?.id === ev.id ? (
              <li key={ev.id}>
                <form onSubmit={handleSave} className="flex gap-2">
                  <input
                    aria-label="Edit highlight"
                    className={inputClass}
                    value={editing.content}
                    maxLength={2000}
                    onChange={(e) => setEditing({ id: ev.id, content: e.target.value })}
                  />
                  <Button type="submit" size="sm" variant="primary" disabled={busy}>
                    Save
                  </Button>
                  <Button size="sm" onClick={() => setEditing(null)}>
                    Cancel
                  </Button>
                </form>
              </li>
            ) : (
              <li
                key={ev.id}
                className="group flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300"
              >
                <span aria-hidden className="mt-2 h-1 w-1 shrink-0 rounded-full bg-zinc-400" />
                <span className="flex-1">
                  {ev.content}{" "}
                  {ORIGIN_NOTE[ev.origin] ? (
                    <Badge tone="ai">{ORIGIN_NOTE[ev.origin]}</Badge>
                  ) : null}{" "}
                  {ev.is_cited ? <Badge tone="lock">Cited in a document</Badge> : null}
                </span>
                <span className="flex shrink-0 gap-1 opacity-100 sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={ev.is_cited}
                    title={
                      ev.is_cited
                        ? "Cited by a generated document; add a new highlight instead"
                        : undefined
                    }
                    onClick={() => setEditing({ id: ev.id, content: ev.content })}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={busy || ev.is_cited}
                    aria-label={`Delete highlight: ${ev.content}`}
                    onClick={() => void run(() => deleteEvidence(ev.id))}
                  >
                    Delete
                  </Button>
                </span>
              </li>
            ),
          )}
        </ul>
      ) : null}
      <form onSubmit={handleAdd} className="mt-2 flex gap-2">
        <label htmlFor={inputId} className="sr-only">
          Add a highlight
        </label>
        <input
          id={inputId}
          className={inputClass}
          placeholder={hint}
          value={draft}
          maxLength={2000}
          onChange={(e) => setDraft(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={busy || !draft.trim()}>
          Add
        </Button>
      </form>
      {error ? (
        <p role="alert" className="mt-1 text-xs text-red-600">
          {error}
        </p>
      ) : null}
    </div>
  );
}
