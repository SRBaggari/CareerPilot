"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Button, Card, EmptyState, Field, FormErrors, inputClass } from "@/components/ui";
import { answerQuestions, listAnswers, type ApplicationAnswer } from "@/lib/api/applicationAnswers";
import { ApiError } from "@/lib/api/client";

import { AnswerCard } from "./AnswerCard";

const EXAMPLES = [
  "Why are you interested in this role?",
  "Describe a relevant project.",
  "Why should we hire you?",
  "Describe your experience with Python.",
];

export function ApplicationQuestionsPage({ jobId }: { jobId: string }) {
  const [answers, setAnswers] = useState<ApplicationAnswer[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [maxWords, setMaxWords] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listAnswers(jobId).then(
      (found) => !cancelled && setAnswers(found),
      (e: unknown) =>
        !cancelled &&
        setLoadError(e instanceof ApiError ? e.message : "Could not load the questions."),
    );
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const questions = text
    .split("\n")
    .map((q) => q.trim())
    .filter(Boolean);

  async function submit() {
    setBusy(true);
    setErrors([]);
    try {
      const limit = maxWords.trim() ? Number(maxWords) : undefined;
      const created = await answerQuestions(jobId, questions, limit);
      setAnswers([...(answers ?? []), ...created]);
      setText("");
    } catch (e) {
      setErrors([e instanceof ApiError ? e.message : "Answering failed. Please try again."]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href={`/jobs/${jobId}`} className="text-sm text-zinc-500 hover:underline">
          ← Job analysis
        </Link>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Application questions
        </h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Answers are written only from your verified evidence, and every statement is checked
          before you see it. Nothing is submitted anywhere: approve each answer when you are happy
          with it.
        </p>
      </div>

      <Card title="Add questions" description="One question per line, as the application asks it.">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <Field id="questions" label="Questions">
            <textarea
              id="questions"
              rows={4}
              className={inputClass}
              value={text}
              placeholder={EXAMPLES.join("\n")}
              onChange={(e) => setText(e.target.value)}
            />
          </Field>
          <Field id="max-words" label="Word limit (optional)" className="max-w-40">
            <input
              id="max-words"
              type="number"
              min={20}
              max={1000}
              className={inputClass}
              value={maxWords}
              onChange={(e) => setMaxWords(e.target.value)}
            />
          </Field>
          <FormErrors errors={errors} />
          <div className="flex flex-wrap gap-2">
            <Button
              type="submit"
              variant="primary"
              disabled={busy || questions.length === 0 || questions.length > 10}
            >
              {busy
                ? "Answering…"
                : `Generate ${questions.length > 1 ? `${questions.length} answers` : "answer"}`}
            </Button>
            <Button onClick={() => setText(EXAMPLES.join("\n"))} disabled={busy}>
              Use example questions
            </Button>
          </div>
        </form>
      </Card>

      {loadError ? (
        <p role="alert" className="text-sm text-red-700">
          {loadError}{" "}
          {loadError.toLowerCase().includes("profile") ? (
            <Link href="/profile" className="underline">
              Go to your profile
            </Link>
          ) : null}
        </p>
      ) : null}
      {answers === null && !loadError ? (
        <p role="status" className="text-sm text-zinc-500">
          Loading…
        </p>
      ) : null}
      {answers?.length === 0 ? <EmptyState>No questions yet.</EmptyState> : null}
      <div className="space-y-4">
        {answers?.map((answer) => (
          <AnswerCard
            key={answer.id}
            answer={answer}
            onChange={(updated) =>
              setAnswers((current) =>
                (current ?? []).map((a) => (a.id === updated.id ? updated : a)),
              )
            }
            onDeleted={(id) => setAnswers((current) => (current ?? []).filter((a) => a.id !== id))}
          />
        ))}
      </div>
    </div>
  );
}
