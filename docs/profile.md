# Candidate Profile

The master profile is the **source of truth for all future AI generation**. Resumes, cover
letters, and matching may use only what is stored here.

- **UI:** `http://localhost:3000/profile`
- **API:** `/api/v1/profile` (interactive docs at `http://localhost:8000/docs`)

## User-provided facts vs AI-generated content

| | User-provided facts | AI-generated content |
| --- | --- | --- |
| Where it lives | `candidate_profiles`, the section tables, `candidate_skills`, `candidate_evidence` | `profile_suggestions` (and, later, generated documents) |
| Who writes it | Only the profile API, on an explicit user action | AI features, via `app.profiles.suggestions.create_suggestion` |
| Validation | Pydantic schemas + database constraints | The **same** schemas, when the suggestion is stored and again when it is accepted |
| Reaches the profile | Immediately | Only when the candidate clicks **Accept** |
| UI | Normal cards | A separate amber "AI suggestions awaiting your review" panel |

How the separation is enforced:

1. **No direct write path.** Nothing in the AI suggestion module writes profile tables except
   `accept_suggestion`. That function is triggered by the user and reuses the regular service
   functions, so AI data meets exactly the same validation as manual edits.
2. **Atomic acceptance.** The suggestion's status change and the profile change commit in one
   transaction. If validation of the merged result fails, neither changes.
3. **Provenance cannot be spoofed.** Request bodies forbid unknown fields, so clients cannot set
   `origin`, `confirmed_at`, `user_id`, or `id`. User-entered evidence is always
   `origin = user_entered`. Accepted AI evidence is recorded as `resume_extracted` or
   `ai_suggested`, is labelled that way in the UI, and has `confirmed_at` set by the acceptance.
4. **Malformed AI output is rejected before storage.** It never reaches the review queue.

## Highlights (evidence)

Each item (project, job, degree, …) has **highlights**: short, concrete statements such as
"Implemented a RAG pipeline using document retrieval and question answering." Each highlight
is a `candidate_evidence` row, and future generated claims must cite these rows.
"Other highlights" holds profile-level facts not tied to one item.

Evidence that a generated document cites cannot be edited or deleted (409). Editing it would
make that document's claims untraceable, so add a new highlight instead. Deleting an item whose
highlights are cited is also refused.

## API

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/profile` | Full profile with nested sections, highlights, skills, `pending_suggestions` count. 404 if none |
| `POST` | `/api/v1/profile` | Create (409 if one exists) |
| `PATCH` | `/api/v1/profile` | Partial update. Omitted fields are unchanged; `null` or `""` clears optional fields; required fields cannot be cleared (422) |
| `DELETE` | `/api/v1/profile` | Deletes the profile and everything attached to it |
| `POST` | `/api/v1/profile/{section}` | `section` is one of `educations`, `work-experiences`, `projects`, `certifications`, `achievements`, `coursework` |
| `PUT` / `DELETE` | `/api/v1/profile/{section}/{id}` | PUT is a full replacement: omitted optional fields are cleared |
| `POST` | `/api/v1/profile/evidence` | `{source_type, subject_id, content}` |
| `PATCH` / `DELETE` | `/api/v1/profile/evidence/{id}` | 409 if cited |
| `POST` | `/api/v1/profile/skills` | Adds from the shared skill vocabulary (case- and whitespace-insensitive, 409 on duplicate) |
| `PUT` / `DELETE` | `/api/v1/profile/skills/{id}` | Proficiency and years only; the shared skill name is never changed |
| `GET` | `/api/v1/profile/suggestions?status_filter=pending` | Review queue |
| `POST` | `/api/v1/profile/suggestions/{id}/accept` or `/reject` | 409 if already decided |

Errors: 404 (not found or not yours), 409 (conflict), 422 (validation, FastAPI's standard
`detail[].loc/msg` shape, field-level where possible).

### Validation highlights

- Strings are trimmed, and blank strings become `null`.
- Lengths match the database columns.
- URLs must be http(s) with a real hostname. A bare `github.com/me` becomes
  `https://github.com/me`. `javascript:`, `data:`, and similar are rejected.
- Emails and phone numbers are checked by format. Emails are lowercased.
- Dates must fall between 1900 and 2100. End dates must not be before start dates.
- A current job cannot have an end date.
- `gpa` requires `gpa_scale`, and `gpa ≤ gpa_scale ≤ 100`.
- Role and location lists are de-duplicated case-insensitively, with at most 20 entries of
  200 characters each.
- Enum values are checked by the API and again by database CHECK constraints.
- Coursework may only link to the candidate's own education entries. Every item lookup is
  scoped to the current user's profile.

## Identity (temporary)

Authentication arrives in a later phase. Until then, `get_current_user` acts as the single
local user named by `DEV_USER_EMAIL` in development and test, and **rejects every request
(401) in production**. Only that dependency will change when Auth.js is added.
