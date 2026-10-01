"use client";

import { useState } from "react";

import { Button, Card, Field, FormErrors, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { paragraphText, saveCoverLetter, type CoverLetter } from "@/lib/api/coverLetters";

/**
 * Edit the letter as text. Saving verifies every sentence first: anything your evidence
 * doesn't support is rejected with a reason next to its paragraph, and nothing is saved.
 */
export function CoverLetterEditor({
  letter,
  onSaved,
  onCancel,
}: {
  letter: CoverLetter;
  onSaved: (letter: CoverLetter) => void;
  onCancel: () => void;
}) {
  const [greeting, setGreeting] = useState(letter.content.greeting);
  const [paragraphs, setParagraphs] = useState(letter.content.paragraphs.map(paragraphText));
  const [closing, setClosing] = useState(letter.content.closing);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    setErrors({});
    setFormErrors([]);
    try {
      onSaved(await saveCoverLetter(letter.id, { greeting, paragraphs, closing }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        setErrors(e.fieldErrors);
        setFormErrors([
          "Some sentences aren't supported by your evidence. Fix or remove them, then save.",
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
    <Card
      title="Edit cover letter"
      description="State only what your evidence shows. To say something new, add it as a highlight in your profile first."
    >
      <div className="space-y-4">
        <FormErrors errors={formErrors} />
        <Field id="letter-greeting" label="Greeting" error={errors.greeting}>
          <input
            id="letter-greeting"
            className={inputClass}
            value={greeting}
            aria-invalid={errors.greeting ? true : undefined}
            onChange={(e) => setGreeting(e.target.value)}
          />
        </Field>
        {paragraphs.map((text, index) => {
          const key = `paragraphs[${index}]`;
          return (
            <div key={index} className="space-y-1">
              <Field
                id={`letter-paragraph-${index}`}
                label={`Paragraph ${index + 1}`}
                error={errors[key]}
              >
                <textarea
                  id={`letter-paragraph-${index}`}
                  rows={4}
                  className={inputClass}
                  value={text}
                  aria-invalid={errors[key] ? true : undefined}
                  onChange={(e) =>
                    setParagraphs(paragraphs.map((p, i) => (i === index ? e.target.value : p)))
                  }
                />
              </Field>
              <Button
                size="sm"
                variant="danger"
                disabled={paragraphs.length === 1}
                onClick={() => setParagraphs(paragraphs.filter((_, i) => i !== index))}
              >
                Remove paragraph {index + 1}
              </Button>
            </div>
          );
        })}
        <Button size="sm" onClick={() => setParagraphs([...paragraphs, ""])}>
          Add paragraph
        </Button>
        <Field id="letter-closing" label="Closing" error={errors.closing}>
          <input
            id="letter-closing"
            className={inputClass}
            value={closing}
            aria-invalid={errors.closing ? true : undefined}
            onChange={(e) => setClosing(e.target.value)}
          />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => void save()} disabled={saving}>
            {saving ? "Verifying…" : "Save"}
          </Button>
          <Button onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        </div>
      </div>
    </Card>
  );
}
