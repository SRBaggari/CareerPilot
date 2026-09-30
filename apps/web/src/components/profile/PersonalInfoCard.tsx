"use client";

import { useState } from "react";

import { Button, Card } from "@/components/ui";
import { updateProfile, type PersonalInfo, type Profile } from "@/lib/api/profile";
import { valuesFromItem, type FieldDef } from "@/lib/profile/form";

import { ItemForm } from "./ItemForm";

export const PERSONAL_FIELDS: FieldDef[] = [
  { name: "full_name", label: "Full name", type: "text", required: true, maxLength: 200 },
  {
    name: "headline",
    label: "Headline",
    type: "text",
    maxLength: 300,
    placeholder: "Final-year CS student focused on ML systems",
  },
  { name: "contact_email", label: "Contact email", type: "email", maxLength: 320 },
  { name: "phone", label: "Phone", type: "text", maxLength: 50 },
  { name: "location", label: "Location", type: "text", maxLength: 200 },
  { name: "website_url", label: "Website", type: "url" },
  { name: "linkedin_url", label: "LinkedIn", type: "url" },
  { name: "github_url", label: "GitHub", type: "url" },
  { name: "summary", label: "Professional summary", type: "textarea", maxLength: 5000, wide: true },
];

function Detail({ label, value, href }: { label: string; value: string | null; href?: boolean }) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-xs text-zinc-500">{label}</dt>
      <dd className="truncate text-sm text-zinc-800 dark:text-zinc-200">
        {href ? (
          <a
            href={value}
            target="_blank"
            rel="noopener noreferrer"
            className="underline-offset-2 hover:underline"
          >
            {value.replace(/^https?:\/\//, "")}
          </a>
        ) : (
          value
        )}
      </dd>
    </div>
  );
}

export function PersonalInfoCard({
  profile,
  onChanged,
}: {
  profile: Profile;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);

  return (
    <Card
      id="personal"
      title="Personal information"
      actions={
        editing ? null : (
          <Button size="sm" onClick={() => setEditing(true)}>
            Edit
          </Button>
        )
      }
    >
      {editing ? (
        <ItemForm
          fields={PERSONAL_FIELDS}
          initial={valuesFromItem(PERSONAL_FIELDS, profile)}
          submitLabel="Save"
          onCancel={() => setEditing(false)}
          onSubmit={async (payload) => {
            await updateProfile(payload as Partial<PersonalInfo>);
            setEditing(false);
            await onChanged();
          }}
        />
      ) : (
        <>
          <p className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            {profile.full_name}
          </p>
          {profile.headline ? (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">{profile.headline}</p>
          ) : null}
          <dl className="mt-4 grid gap-3 sm:grid-cols-3">
            <Detail label="Email" value={profile.contact_email} />
            <Detail label="Phone" value={profile.phone} />
            <Detail label="Location" value={profile.location} />
            <Detail label="Website" value={profile.website_url} href />
            <Detail label="LinkedIn" value={profile.linkedin_url} href />
            <Detail label="GitHub" value={profile.github_url} href />
          </dl>
          {profile.summary ? (
            <p className="mt-4 text-sm whitespace-pre-line text-zinc-700 dark:text-zinc-300">
              {profile.summary}
            </p>
          ) : null}
        </>
      )}
    </Card>
  );
}
