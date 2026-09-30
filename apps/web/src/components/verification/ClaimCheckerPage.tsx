"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, Card, Field, FormErrors, inputClass } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { checkClaims, type VerificationReport } from "@/lib/api/verification";

import { VerificationReportView } from "./VerificationReportView";

/**
 * Check any text (a summary, bullets, a cover letter paragraph) against your verified
 * evidence and profile. Nothing is saved.
 */
export function ClaimCheckerPage() {
  const [text, setText] = useState("");
  const [report, setReport] = useState<VerificationReport | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    setErrors([]);
    try {
      setReport(await checkClaims(text));
    } catch (e) {
      setReport(null);
      setErrors([e instanceof ApiError ? e.message : "Verification failed. Please try again."]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Check claims
        </h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Paste statements about yourself. Each sentence is checked against your verified evidence
          and your{" "}
          <Link href="/profile" className="underline">
            profile
          </Link>
          : supported, partially supported, unsupported, or contradicted.
        </p>
      </div>
      <Card title="Statements to check">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void check();
          }}
        >
          <Field id="claims-text" label="Text" hint="One claim per sentence or bullet.">
            <textarea
              id="claims-text"
              rows={6}
              className={inputClass}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Built a RAG-based research assistant."
            />
          </Field>
          <FormErrors errors={errors} />
          <Button type="submit" variant="primary" disabled={busy || !text.trim()}>
            {busy ? "Checking…" : "Check claims"}
          </Button>
        </form>
      </Card>
      {report ? (
        <Card title="Verification report">
          <VerificationReportView report={report} />
        </Card>
      ) : null}
    </div>
  );
}
