"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

import { WorkflowSteps } from "@/components/WorkflowSteps";
import { Badge, Card } from "@/components/ui";
import { publicConfig } from "@/lib/config";
import { listSources, type JobSourceStatus } from "@/lib/api/discovery";
import { getHealth, getReadiness, type Health, type Readiness } from "@/lib/api/health";
import { getProfile, type Profile } from "@/lib/api/profile";
import { chooseTheme, storedTheme, subscribeTheme, THEMES } from "@/lib/theme";
import { WORKFLOW_STEPS } from "@/lib/workflow";

const GUARANTEES = [
  "Everything written about you comes from evidence you provided, and every claim is verified against it.",
  "Nothing is submitted without your explicit approval of the exact content.",
  "Browser assistance stops at logins, CAPTCHAs and access controls; it never works around them.",
  "Job discovery uses official APIs and feeds only; no scraping of sites that prohibit it.",
  "API keys and other secrets are never written to logs.",
];

function Status({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className={`h-2 w-2 rounded-full ${ok === null ? "bg-zinc-300" : ok ? "bg-emerald-500" : "bg-red-500"}`}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

export function SettingsPage() {
  const theme = useSyncExternalStore(subscribeTheme, storedTheme, () => "system" as const);
  const [profile, setProfile] = useState<Profile | null | undefined>(undefined);
  const [health, setHealth] = useState<Health | null | false>(null);
  const [ready, setReady] = useState<Readiness | null | false>(null);
  const [sources, setSources] = useState<JobSourceStatus[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProfile().then(
      (p) => !cancelled && setProfile(p),
      () => !cancelled && setProfile(null),
    );
    getHealth().then(
      (h) => !cancelled && setHealth(h),
      () => !cancelled && setHealth(false),
    );
    getReadiness().then(
      (r) => !cancelled && setReady(r),
      () => !cancelled && setReady(false),
    );
    listSources().then(
      (s) => !cancelled && setSources(s),
      () => !cancelled && setSources([]),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 space-y-6 px-4 py-6 sm:px-6 sm:py-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Settings
        </h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Appearance, your account, and how CareerPilot is connected.
        </p>
      </div>

      <Card id="appearance" title="Appearance" description="Saved in this browser.">
        <fieldset>
          <legend className="sr-only">Theme</legend>
          <div className="flex flex-wrap gap-2">
            {THEMES.map((t) => (
              <label
                key={t.value}
                className="flex cursor-pointer items-center gap-2 rounded-md border border-zinc-300 px-3 py-2 text-sm has-checked:border-zinc-900 has-checked:bg-zinc-50 dark:border-zinc-700 dark:has-checked:border-zinc-100 dark:has-checked:bg-zinc-900"
              >
                <input
                  type="radio"
                  name="theme"
                  value={t.value}
                  checked={theme === t.value}
                  onChange={() => chooseTheme(t.value)}
                />
                {t.label}
              </label>
            ))}
          </div>
        </fieldset>
      </Card>

      <Card id="account" title="Account">
        {profile === undefined ? (
          <p role="status" className="text-sm text-zinc-500">
            Loading…
          </p>
        ) : profile ? (
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[8rem_1fr]">
            <dt className="text-zinc-500">Name</dt>
            <dd>{profile.full_name}</dd>
            <dt className="text-zinc-500">Contact email</dt>
            <dd>{profile.contact_email ?? "Not set"}</dd>
          </dl>
        ) : (
          <p className="text-sm">No profile yet.</p>
        )}
        <p className="mt-3 text-sm">
          <Link href="/profile" className="font-medium underline">
            Edit your profile
          </Link>
        </p>
      </Card>

      <Card id="connection" title="Connection">
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-[8rem_1fr]">
          <dt className="text-zinc-500">API</dt>
          <dd className="break-all">
            <Status
              ok={health === null ? null : health !== false}
              label={
                health === null
                  ? "Checking…"
                  : health
                    ? `Online (${health.environment})`
                    : `Unreachable at ${publicConfig.apiBaseUrl}`
              }
            />
          </dd>
          <dt className="text-zinc-500">Database</dt>
          <dd>
            <Status
              ok={ready === null ? null : ready !== false && ready.status === "ready"}
              label={
                ready === null
                  ? "Checking…"
                  : ready && ready.status === "ready"
                    ? "Connected, vector search available"
                    : "Not ready"
              }
            />
          </dd>
        </dl>
      </Card>

      <Card
        id="sources"
        title="Job sources"
        description="Where job discovery may look. Each source is checked against its access rules."
      >
        {sources === null ? (
          <p role="status" className="text-sm text-zinc-500">
            Loading…
          </p>
        ) : sources.length === 0 ? (
          <p className="text-sm text-zinc-500">No job sources configured.</p>
        ) : (
          <ul className="space-y-2 text-sm" aria-label="Job sources">
            {sources.map((s) => (
              <li key={s.name}>
                <span className="font-medium">{s.display_name}</span>{" "}
                {s.enabled ? <Badge tone="lock">Enabled</Badge> : <Badge>Disabled</Badge>}
                {!s.enabled && s.reasons.length ? (
                  <span className="block text-xs text-zinc-500">{s.reasons.join(" ")}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        id="safety"
        title="Safety and privacy"
        description="What CareerPilot always does, and never does."
      >
        <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-700 dark:text-zinc-300">
          {GUARANTEES.map((g) => (
            <li key={g}>{g}</li>
          ))}
        </ul>
      </Card>

      <Card id="how" title="How CareerPilot works">
        <WorkflowSteps steps={WORKFLOW_STEPS} />
      </Card>
    </main>
  );
}
