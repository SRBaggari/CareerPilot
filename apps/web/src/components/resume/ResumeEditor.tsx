"use client";

import { useState } from "react";

import { Button, Card, FormErrors, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  claimKey,
  saveTailoredResume,
  type Claim,
  type ResumeContent,
  type TailoredResume,
} from "@/lib/api/tailoredResumes";

function move<T>(items: T[], index: number, by: -1 | 1): T[] {
  const next = [...items];
  const target = index + by;
  if (target < 0 || target >= next.length) return items;
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

function ClaimListEditor({
  label,
  section,
  claims,
  errors,
  onChange,
}: {
  label: string;
  section: string;
  claims: Claim[];
  errors: Record<string, string>;
  onChange: (claims: Claim[]) => void;
}) {
  if (claims.length === 0) return <p className="text-sm text-zinc-500 italic">No statements.</p>;
  return (
    <ol className="space-y-3">
      {claims.map((claim, index) => {
        const id = `${section}-${index}`;
        const error = errors[claimKey(section, index)];
        return (
          <li key={claim.claim_id ?? id} className="space-y-1">
            <label htmlFor={id} className="sr-only">
              {label} {index + 1}
            </label>
            <textarea
              id={id}
              rows={2}
              value={claim.text}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? `${id}-error` : undefined}
              className={inputClass}
              onChange={(e) =>
                onChange(claims.map((c, i) => (i === index ? { ...c, text: e.target.value } : c)))
              }
            />
            {error ? (
              <p id={`${id}-error`} role="alert" className="text-xs text-red-600 dark:text-red-400">
                {error}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-1">
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Move ${label.toLowerCase()} ${index + 1} up`}
                disabled={index === 0}
                onClick={() => onChange(move(claims, index, -1))}
              >
                ↑
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Move ${label.toLowerCase()} ${index + 1} down`}
                disabled={index === claims.length - 1}
                onClick={() => onChange(move(claims, index, 1))}
              >
                ↓
              </Button>
              <Button
                size="sm"
                variant="danger"
                aria-label={`Remove ${label.toLowerCase()} ${index + 1}`}
                onClick={() => onChange(claims.filter((_, i) => i !== index))}
              >
                Remove
              </Button>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Edit wording, order, and what to include. Names, employers, titles, and dates can't be
 * edited here: they come from the profile. Every statement is re-verified on save.
 */
export function ResumeEditor({
  resume,
  onSaved,
  onCancel,
}: {
  resume: TailoredResume;
  onSaved: (resume: TailoredResume) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<ResumeContent>(resume.content);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    setErrors({});
    setFormErrors([]);
    try {
      onSaved(await saveTailoredResume(resume.id, draft));
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        setErrors(e.fieldErrors);
        setFormErrors([
          "Some statements aren't supported by your evidence. Fix or remove them, then save.",
          ...e.formErrors,
        ]);
      } else {
        setFormErrors([e instanceof ApiError ? e.message : "Saving failed. Please try again."]);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <FormErrors errors={formErrors} />
      <Card
        title="Summary"
        description="Each sentence must say only what the evidence it cites says."
      >
        <ClaimListEditor
          label="Summary sentence"
          section="summary"
          claims={draft.summary}
          errors={errors}
          onChange={(summary) => setDraft({ ...draft, summary })}
        />
      </Card>
      <Card title="Skills" description="Remove skills you don't want to lead with.">
        {draft.skills.length === 0 ? (
          <p className="text-sm text-zinc-500 italic">No skills.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {draft.skills.map((skill, index) => (
              <li
                key={skill.text}
                className="flex items-center gap-1 rounded-full border border-zinc-300 py-0.5 pr-1 pl-3 text-sm dark:border-zinc-700"
              >
                {skill.text}
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove skill ${skill.text}`}
                  onClick={() =>
                    setDraft({ ...draft, skills: draft.skills.filter((_, i) => i !== index) })
                  }
                >
                  ×
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {draft.experience.map((job, jobIndex) => (
        <Card
          key={job.record_id}
          title={`${job.title}, ${job.company_name}`}
          description="Title, employer, and dates come from your profile."
        >
          <ClaimListEditor
            label="Bullet"
            section={`experience:${job.record_id}`}
            claims={job.bullets}
            errors={errors}
            onChange={(bullets) =>
              setDraft({
                ...draft,
                experience: draft.experience.map((j, i) =>
                  i === jobIndex ? { ...j, bullets } : j,
                ),
              })
            }
          />
        </Card>
      ))}
      {draft.projects.map((project, index) => (
        <Card
          key={project.record_id}
          title={`Project: ${project.title}`}
          actions={
            <>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Move project ${project.title} up`}
                disabled={index === 0}
                onClick={() => setDraft({ ...draft, projects: move(draft.projects, index, -1) })}
              >
                ↑
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Move project ${project.title} down`}
                disabled={index === draft.projects.length - 1}
                onClick={() => setDraft({ ...draft, projects: move(draft.projects, index, 1) })}
              >
                ↓
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={() =>
                  setDraft({ ...draft, projects: draft.projects.filter((_, i) => i !== index) })
                }
              >
                Remove project
              </Button>
            </>
          }
        >
          <ClaimListEditor
            label="Bullet"
            section={`projects:${project.record_id}`}
            claims={project.bullets}
            errors={errors}
            onChange={(bullets) =>
              setDraft({
                ...draft,
                projects: draft.projects.map((p, i) => (i === index ? { ...p, bullets } : p)),
              })
            }
          />
        </Card>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => void save()} disabled={saving}>
          {saving ? "Verifying…" : "Save changes"}
        </Button>
        <Button onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
