"use client";

import { useState } from "react";

import { Button, Card, EmptyState } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { addSkill, deleteSkill, updateSkill, type Skill } from "@/lib/api/profile";
import { emptyValues, valuesFromItem, type FieldDef } from "@/lib/profile/form";
import {
  label,
  PROFICIENCY_LABELS,
  PROFICIENCY_OPTIONS,
  SKILL_CATEGORY_LABELS,
  SKILL_CATEGORY_OPTIONS,
} from "@/lib/profile/options";

import { ItemForm } from "./ItemForm";

const LEVEL_FIELDS: FieldDef[] = [
  { name: "proficiency", label: "Proficiency", type: "select", options: PROFICIENCY_OPTIONS },
  { name: "years_experience", label: "Years of experience", type: "number", step: "0.5" },
];
const ADD_FIELDS: FieldDef[] = [
  {
    name: "name",
    label: "Skill",
    type: "text",
    required: true,
    maxLength: 100,
    placeholder: "e.g. PyTorch",
  },
  { name: "category", label: "Category", type: "select", options: SKILL_CATEGORY_OPTIONS },
  ...LEVEL_FIELDS,
];

function describe(skill: Skill): string {
  const years = skill.years_experience ? `${Number(skill.years_experience)} yr` : "";
  return [label(PROFICIENCY_LABELS, skill.proficiency), years].filter(Boolean).join(" · ");
}

export function SkillsCard({
  skills,
  onChanged,
}: {
  skills: Skill[];
  onChanged: () => Promise<void>;
}) {
  const [mode, setMode] = useState<
    { kind: "idle" } | { kind: "add" } | { kind: "edit"; skill: Skill }
  >({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);

  async function remove(skill: Skill) {
    setError(null);
    try {
      await deleteSkill(skill.id);
      await onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not remove skill.");
    }
  }

  return (
    <Card
      id="skills"
      title="Skills"
      description="Back each skill with highlights on your projects or experience."
      actions={
        mode.kind === "idle" ? (
          <Button size="sm" onClick={() => setMode({ kind: "add" })}>
            + Add skill
          </Button>
        ) : null
      }
    >
      {error ? (
        <p role="alert" className="mb-3 text-sm text-red-600">
          {error}
        </p>
      ) : null}
      {mode.kind !== "idle" ? (
        <div className="mb-4 rounded-lg border border-dashed border-zinc-300 p-4 dark:border-zinc-700">
          {mode.kind === "add" ? (
            <ItemForm
              fields={ADD_FIELDS}
              initial={emptyValues(ADD_FIELDS)}
              submitLabel="Add skill"
              onCancel={() => setMode({ kind: "idle" })}
              onSubmit={async (payload) => {
                await addSkill(payload as Parameters<typeof addSkill>[0]);
                setMode({ kind: "idle" });
                await onChanged();
              }}
            />
          ) : (
            <>
              <p className="mb-3 text-sm font-medium">{mode.skill.name}</p>
              <ItemForm
                fields={LEVEL_FIELDS}
                initial={valuesFromItem(LEVEL_FIELDS, mode.skill)}
                submitLabel="Save"
                onCancel={() => setMode({ kind: "idle" })}
                onSubmit={async (payload) => {
                  await updateSkill(mode.skill.id, payload as Parameters<typeof updateSkill>[1]);
                  setMode({ kind: "idle" });
                  await onChanged();
                }}
              />
            </>
          )}
        </div>
      ) : null}
      {skills.length === 0 ? (
        <EmptyState>No skills added yet.</EmptyState>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {skills.map((skill) => (
            <li
              key={skill.id}
              className="flex items-center gap-1 rounded-lg border border-zinc-200 py-1 pr-1 pl-3 text-sm dark:border-zinc-800"
              title={label(SKILL_CATEGORY_LABELS, skill.category) || undefined}
            >
              <span className="font-medium text-zinc-900 dark:text-zinc-100">{skill.name}</span>
              {describe(skill) ? (
                <span className="text-xs text-zinc-500">{describe(skill)}</span>
              ) : null}
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Edit ${skill.name}`}
                onClick={() => setMode({ kind: "edit", skill })}
              >
                Edit
              </Button>
              <Button
                size="sm"
                variant="danger"
                aria-label={`Remove ${skill.name}`}
                onClick={() => void remove(skill)}
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
