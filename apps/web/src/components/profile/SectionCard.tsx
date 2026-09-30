"use client";

import { useState } from "react";

import { Button, Card, EmptyState } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { createItem, deleteItem, replaceItem, type SectionItem } from "@/lib/api/profile";
import { emptyValues, valuesFromItem } from "@/lib/profile/form";
import type { SectionDef } from "@/lib/profile/sections";

import { EvidenceList } from "./EvidenceList";
import { ItemForm } from "./ItemForm";

type Props = {
  section: SectionDef;
  items: SectionItem[];
  educations: SectionItem[];
  onChanged: () => Promise<void>;
};

export function SectionCard({ section, items, educations, onChanged }: Props) {
  const [mode, setMode] = useState<
    { kind: "idle" } | { kind: "add" } | { kind: "edit"; id: string }
  >({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);

  async function handleDelete(item: SectionItem) {
    const { title } = section.summarize(item);
    if (!window.confirm(`Delete "${title}" and its highlights? This cannot be undone.`)) return;
    setError(null);
    try {
      await deleteItem(section.path, item.id);
      await onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not delete.");
    }
  }

  return (
    <Card
      id={section.key}
      title={section.title}
      actions={
        mode.kind === "idle" ? (
          <Button size="sm" onClick={() => setMode({ kind: "add" })}>
            + Add {section.singular}
          </Button>
        ) : null
      }
    >
      {error ? (
        <p role="alert" className="mb-3 text-sm text-red-600">
          {error}
        </p>
      ) : null}

      {mode.kind === "add" ? (
        <div className="mb-4 rounded-lg border border-dashed border-zinc-300 p-4 dark:border-zinc-700">
          <ItemForm
            fields={section.fields}
            initial={emptyValues(section.fields)}
            submitLabel={`Add ${section.singular}`}
            educations={educations}
            onCancel={() => setMode({ kind: "idle" })}
            onSubmit={async (payload) => {
              await createItem(section.path, { ...payload, sort_order: items.length });
              setMode({ kind: "idle" });
              await onChanged();
            }}
          />
        </div>
      ) : null}

      {items.length === 0 && mode.kind !== "add" ? (
        <EmptyState>No {section.title.toLowerCase()} added yet.</EmptyState>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {items.map((item) => {
            const summary = section.summarize(item);
            return (
              <li key={item.id} className="py-4 first:pt-0 last:pb-0">
                {mode.kind === "edit" && mode.id === item.id ? (
                  <ItemForm
                    fields={section.fields}
                    initial={valuesFromItem(section.fields, item)}
                    submitLabel="Save changes"
                    educations={educations}
                    onCancel={() => setMode({ kind: "idle" })}
                    onSubmit={async (payload) => {
                      await replaceItem(section.path, item.id, {
                        ...payload,
                        sort_order: item.sort_order,
                      });
                      setMode({ kind: "idle" });
                      await onChanged();
                    }}
                  />
                ) : (
                  <>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="font-medium text-zinc-900 dark:text-zinc-50">
                          {summary.title}
                        </h3>
                        {summary.subtitle ? (
                          <p className="text-sm text-zinc-600 dark:text-zinc-400">
                            {summary.subtitle}
                          </p>
                        ) : null}
                        {summary.meta ? (
                          <p className="text-xs text-zinc-500">{summary.meta}</p>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setMode({ kind: "edit", id: item.id })}
                          aria-label={`Edit ${summary.title}`}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => void handleDelete(item)}
                          aria-label={`Delete ${summary.title}`}
                        >
                          Delete
                        </Button>
                      </div>
                    </div>
                    {typeof item.description === "string" && item.description ? (
                      <p className="mt-2 text-sm whitespace-pre-line text-zinc-700 dark:text-zinc-300">
                        {item.description}
                      </p>
                    ) : null}
                    <EvidenceList
                      evidence={item.evidence}
                      sourceType={section.evidenceType}
                      subjectId={item.id}
                      hint={section.evidenceHint}
                      onChanged={onChanged}
                    />
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
