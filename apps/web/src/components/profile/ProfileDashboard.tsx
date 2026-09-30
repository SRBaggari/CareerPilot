"use client";

import { useCallback, useEffect, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { Button, Card } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { createProfile, deleteProfile, getProfile, type Profile } from "@/lib/api/profile";
import { emptyValues } from "@/lib/profile/form";
import { SECTIONS } from "@/lib/profile/sections";

import { EvidenceList } from "./EvidenceList";
import { ExtractedInfoReview } from "./ExtractedInfoReview";
import { ItemForm } from "./ItemForm";
import { PERSONAL_FIELDS, PersonalInfoCard } from "./PersonalInfoCard";
import { PreferencesCard } from "./PreferencesCard";
import { ResumeCard } from "./ResumeCard";
import { SectionCard } from "./SectionCard";
import { SkillsCard } from "./SkillsCard";

type State =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "error"; message: string }
  | { status: "ready"; profile: Profile };

const NAV = [
  { href: "#resume", label: "Resume" },
  { href: "#personal", label: "Personal" },
  { href: "#preferences", label: "Preferences" },
  { href: "#skills", label: "Skills" },
  ...SECTIONS.map((s) => ({ href: `#${s.key}`, label: s.title })),
  { href: "#general", label: "Other highlights" },
];

async function fetchState(): Promise<State> {
  try {
    const profile = await getProfile();
    return profile ? { status: "ready", profile } : { status: "missing" };
  } catch (e) {
    return {
      status: "error",
      message: e instanceof ApiError ? e.message : "Could not load profile.",
    };
  }
}

export function ProfileDashboard() {
  const [state, setState] = useState<State>({ status: "loading" });

  const reload = useCallback(async () => setState(await fetchState()), []);

  useEffect(() => {
    let cancelled = false;
    void fetchState().then((next) => !cancelled && setState(next));
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleDeleteProfile() {
    const ok = window.confirm(
      "Delete your entire master profile, including all sections and highlights? This cannot be undone.",
    );
    if (!ok) return;
    try {
      await deleteProfile();
      setState({ status: "missing" });
    } catch (e) {
      window.alert(e instanceof ApiError ? e.message : "Could not delete the profile.");
    }
  }

  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <AppHeader current="/profile" />

      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        {state.status === "loading" ? (
          <p className="text-sm text-zinc-500" role="status">
            Loading your profile…
          </p>
        ) : state.status === "error" ? (
          <div
            role="alert"
            className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-800"
          >
            <p className="font-medium">Could not load your profile</p>
            <p className="mt-1">{state.message}</p>
            <Button className="mt-3" onClick={() => void reload()}>
              Retry
            </Button>
          </div>
        ) : state.status === "missing" ? (
          <div className="mx-auto max-w-2xl">
            <Card
              title="Create your master profile"
              description="Start with the basics. You can add education, experience, projects, and more next."
            >
              <ItemForm
                fields={PERSONAL_FIELDS}
                initial={emptyValues(PERSONAL_FIELDS)}
                submitLabel="Create profile"
                onSubmit={async (payload) => {
                  const profile = await createProfile(
                    payload as Parameters<typeof createProfile>[0],
                  );
                  setState({ status: "ready", profile });
                }}
              />
            </Card>
          </div>
        ) : (
          <div className="grid gap-8 lg:grid-cols-[180px_1fr]">
            <nav aria-label="Profile sections" className="hidden lg:block">
              <ul className="sticky top-20 space-y-1 text-sm">
                {NAV.map((n) => (
                  <li key={n.href}>
                    <a
                      href={n.href}
                      className="block rounded px-2 py-1 text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900"
                    >
                      {n.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>

            <div className="min-w-0 space-y-6">
              <div className="rounded-xl border border-zinc-200 bg-white px-5 py-4 text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
                <p className="font-medium text-zinc-900 dark:text-zinc-100">Your source of truth</p>
                <p className="mt-1">
                  Everything on this page is information <strong>you</strong> provided. CareerPilot
                  will only use these facts — and the highlights under each item — when tailoring
                  resumes and cover letters. AI output never changes this profile unless you accept
                  it.
                </p>
              </div>

              <ResumeCard onChanged={reload} />
              <ExtractedInfoReview count={state.profile.pending_suggestions} onChanged={reload} />
              <PersonalInfoCard profile={state.profile} onChanged={reload} />
              <PreferencesCard
                key={state.profile.updated_at}
                profile={state.profile}
                onChanged={reload}
              />
              <SkillsCard skills={state.profile.skills} onChanged={reload} />
              {SECTIONS.map((section) => (
                <SectionCard
                  key={section.key}
                  section={section}
                  items={state.profile[section.key]}
                  educations={state.profile.educations}
                  onChanged={reload}
                />
              ))}
              <Card
                id="general"
                title="Other highlights"
                description="Facts about you that don't belong to a single item, e.g. languages or leadership."
              >
                <EvidenceList
                  evidence={state.profile.evidence}
                  sourceType="profile"
                  subjectId={null}
                  hint="e.g. Fluent in English, Hindi, and Telugu."
                  onChanged={reload}
                />
              </Card>

              <section
                aria-labelledby="danger-heading"
                className="rounded-xl border border-red-200 p-5 dark:border-red-900"
              >
                <h2
                  id="danger-heading"
                  className="text-base font-semibold text-red-800 dark:text-red-300"
                >
                  Delete profile
                </h2>
                <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                  Permanently removes your master profile and everything attached to it.
                </p>
                <Button
                  variant="danger"
                  className="mt-3 border border-red-300"
                  onClick={() => void handleDeleteProfile()}
                >
                  Delete my profile
                </Button>
              </section>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
