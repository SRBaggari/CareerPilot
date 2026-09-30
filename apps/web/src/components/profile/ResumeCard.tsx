"use client";

import { useEffect, useId, useRef, useState, type DragEvent } from "react";

import { Badge, Button, Card, EmptyState } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import {
  ACCEPTED_TYPES,
  MAX_RESUME_MB,
  deleteResume,
  getResume,
  listResumes,
  uploadResume,
  type Resume,
} from "@/lib/api/resumes";

const formatSize = (bytes: number) =>
  bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

function clientSideProblem(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!name.endsWith(".pdf") && !name.endsWith(".docx")) return "Upload a PDF or DOCX file.";
  if (file.size > MAX_RESUME_MB * 1024 * 1024)
    return `The file is larger than ${MAX_RESUME_MB} MB.`;
  return null;
}

/** Upload a resume and see how each upload was processed. */
export function ResumeCard({ onChanged }: { onChanged: () => Promise<void> }) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [resumes, setResumes] = useState<Resume[] | null>(null);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [textFor, setTextFor] = useState<{ id: string; text: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    listResumes().then(
      (items) => !cancelled && setResumes(items),
      () => !cancelled && setResumes([]),
    );
    return () => {
      cancelled = true;
    };
  }, [version]);

  async function upload(file: File) {
    const problem = clientSideProblem(file);
    setError(problem);
    if (problem) return;
    setBusy(true);
    try {
      await uploadResume(file);
      setVersion((v) => v + 1);
      await onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function remove(resume: Resume) {
    const pending = resume.pending_suggestions
      ? ` Its ${resume.pending_suggestions} unreviewed item(s) will be discarded.`
      : "";
    if (
      !window.confirm(
        `Delete ${resume.file_name}?${pending} Information you already accepted stays in your profile.`,
      )
    )
      return;
    try {
      await deleteResume(resume.id);
      setVersion((v) => v + 1);
      await onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not delete the resume.");
    }
  }

  async function toggleText(resume: Resume) {
    if (textFor?.id === resume.id) return setTextFor(null);
    try {
      const detail = await getResume(resume.id);
      setTextFor({ id: resume.id, text: detail.parsed_text ?? "" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the extracted text.");
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void upload(file);
  }

  return (
    <Card
      id="resume"
      title="Master resume"
      description="Upload your resume to pre-fill your profile. You review every extracted item before it's saved."
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm ${
          dragging
            ? "border-zinc-500 bg-zinc-50 dark:bg-zinc-900"
            : "border-zinc-300 dark:border-zinc-700"
        }`}
      >
        <p className="text-zinc-600 dark:text-zinc-400">
          Drag a PDF or DOCX here (max {MAX_RESUME_MB} MB), or
        </p>
        <label htmlFor={inputId} className="sr-only">
          Choose resume file
        </label>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={ACCEPTED_TYPES}
          className="hidden"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
          }}
        />
        <Button variant="primary" disabled={busy} onClick={() => inputRef.current?.click()}>
          {busy ? "Reading your resume…" : "Choose file"}
        </Button>
      </div>
      {error ? (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {error}
        </p>
      ) : null}

      <div className="mt-4">
        {resumes === null ? null : resumes.length === 0 ? (
          <EmptyState>No resumes uploaded yet.</EmptyState>
        ) : (
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {resumes.map((r) => (
              <li key={r.id} className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-zinc-900 dark:text-zinc-100">
                    {r.file_name}
                  </span>
                  <span className="text-xs text-zinc-500">
                    {r.file_format.toUpperCase()} · {formatSize(r.file_size_bytes)}
                  </span>
                  {r.is_primary ? <Badge>Primary</Badge> : null}
                  {r.parse_status === "failed" ? (
                    <Badge tone="ai">Couldn&apos;t read</Badge>
                  ) : r.pending_suggestions > 0 ? (
                    <Badge tone="ai">{r.pending_suggestions} to review</Badge>
                  ) : (
                    <Badge tone="lock">Reviewed</Badge>
                  )}
                  <span className="ml-auto flex gap-1">
                    {r.parse_status === "parsed" ? (
                      <Button size="sm" variant="ghost" onClick={() => void toggleText(r)}>
                        {textFor?.id === r.id ? "Hide text" : "View extracted text"}
                      </Button>
                    ) : null}
                    <Button
                      size="sm"
                      variant="danger"
                      aria-label={`Delete ${r.file_name}`}
                      onClick={() => void remove(r)}
                    >
                      Delete
                    </Button>
                  </span>
                </div>
                {r.parse_error ? (
                  <p className="mt-1 text-sm text-red-600">{r.parse_error}</p>
                ) : null}
                {r.parse_warnings.length > 0 ? (
                  <ul className="mt-1 list-disc pl-5 text-xs text-amber-800 dark:text-amber-300">
                    {r.parse_warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                ) : null}
                {textFor?.id === r.id ? (
                  <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-zinc-50 p-3 text-xs whitespace-pre-wrap text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                    {textFor.text}
                  </pre>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
