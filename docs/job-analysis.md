# Job Description Analysis

Turns a job posting into structured, stored information, reporting **only what the posting
states**. Code: `apps/api/app/jobs/`, UI: `/jobs` and `/jobs/[id]`.

## Adding a job

| How | Endpoint | Notes |
| --- | --- | --- |
| **Paste** a description | `POST /api/v1/jobs/analyze` `{description, source_url?, title?, company_name?, location?}` | Analyzed and stored. `title`, `company_name`, and `location` are optional corrections and take precedence |
| **Job URL** | `source_url` on either endpoint | Stored for reference only and **never fetched**. Automatic scraping isn't supported, so the description must be pasted |
| **Manual entry** | `POST /api/v1/jobs` | Fields plus a requirement list the user classifies. Nothing is extracted or inferred |

If the title or company can't be found in a pasted description, the request fails with a 422
naming both fields. The UI then shows them for the user to fill in. Any AI call that already
happened is still logged.

Other endpoints: `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`, and `DELETE /api/v1/jobs/{id}`.
Deletion returns 409 when documents or an application reference the job. Jobs are private to
the user who added them.

## What is extracted

| Field | Rule |
| --- | --- |
| Company, job title, location | Labels (`Company:`, `Job Title:`, `Location:`), the first line ("Title at Company", "Title – Company"), or an "About <Company>" heading |
| Work mode | Remote / hybrid / on-site, only when exactly one is stated. "Hybrid (3 days in office)" counts as hybrid; "remote or hybrid" gives none, with a warning |
| Employment type | Full-time, part-time, internship, contract, freelance, volunteer, only when unambiguous |
| Required and preferred skills | Skill statements and named technologies (one row per technology, linked to the shared skill vocabulary) |
| Technologies | Named only when literally present (aliases such as `k8s` and `Postgres` are recognized; "Go" only as "Golang") |
| Education, experience (with minimum years), certifications, language, eligibility | Typed by wording, e.g. "graduating in 2026" or "authorized to work" → eligibility |
| Responsibilities | Always **informational** |
| Salary | **Only when stated.** Verbatim text plus min/max/period when unambiguous. Currency only when explicit (`USD`, `€`, `₹`, `Rs`, `LPA`); a bare `$` gives no currency. Funding amounts ("raised $50M") are ignored |
| Application deadline | **Only when stated** next to deadline wording ("Apply by", "Last date to apply", "Applications close"). Numeric dates are used only when day and month order is unambiguous (`15/08/2026` yes, `05/06/2026` no, with a warning) |

## REQUIRED / PREFERRED / INFORMATIONAL

Importance comes **only from the text**:

- **Required:** under a requirements-type heading ("Requirements", "Qualifications", "What
  you'll need", "Eligibility", …) or stated with *must / required / minimum / mandatory / at
  least*.
- **Preferred:** under "Nice to have", "Preferred qualifications", "Good to have", … or stated
  with *preferred / a plus / bonus / ideally / would be nice*. This outranks the section, so
  "…is a plus" under "Requirements" is preferred.
- **Informational:** responsibilities, company or team descriptions, tech-stack blurbs,
  benefits, and any statement whose status isn't stated. A technology named only in "Our stack
  includes Kafka" is informational, not required.

Nothing is promoted to required without an explicit cue.

## Structured LLM output

When `ANTHROPIC_API_KEY` is set (`JOB_ANALYZER=auto`), Claude extracts the job into a **strict
JSON schema**: every object has `additionalProperties: false`, every field is required
(nullable where information may be missing), and categories and importance are closed enums.
The prompt requires verbatim copying and forbids inference. On any failure the rule-based
analyzer is used instead, with a warning. Calls are logged in `ai_execution_logs`.

## Guarding against inference

Both analyzers' output passes through `grounding.py`:

1. Every requirement's text and verbatim `source_excerpt` must appear in the description.
   Technologies must be named in their own excerpt. Invented requirements are dropped.
2. Title, company, and location must appear in the text. Salary numbers, currency, and period
   must be backed by the verbatim salary text. A deadline must be backed by its verbatim
   sentence.
3. **Importance is capped by the description's structure:** anything under a
   responsibilities, about, tech-stack, or benefits heading becomes informational, and anything
   under a "Nice to have" heading or with a preferred cue can be at most preferred. A model can't
   turn "Mentor junior engineers" into a required skill.
4. Minimum years must appear in the excerpt.

Dropped and downgraded values are reported in `analysis_warnings` and shown in the UI.

## Storage (migration `0006`)

- `jobs` gains `salary_period`, `salary_text`, `application_deadline`, `input_method`
  (`pasted_text` / `manual_entry`), `analyzer_name`, `analysis_warnings`, and `analyzed_at`.
  `description` is now nullable (manual entries).
- `job_requirements` gains `source_excerpt`. `requirement_type` adds `technology` and
  `eligibility`, and `importance` adds `informational`.

## UI

- `/jobs`: a **Paste description / Enter manually** form, plus the list of your jobs with
  their requirement counts.
- `/jobs/[id]`: **Job overview**, **Required skills**, **Preferred skills**, **Education**,
  **Experience**, **Responsibilities**, **Eligibility** (plus Certifications and "Also
  mentioned" when present), the analysis warnings, and the original description. Every item
  shows its importance badge and can reveal the verbatim sentence it came from. Missing
  information is shown as "Not stated", never filled in.

## Tests

Three sample descriptions in `apps/api/tests/fixtures/jobs/`: a US full-time role with
headings, an Indian internship written with labels (stipend, CTC in LPA, eligibility), and a
prose posting without headings.

- `tests/test_job_analysis.py`: per-sample expectations, the salary, deadline, work-mode and
  employment parsers, technology names, headings, the LLM mapping, schema strictness, and
  grounding against invented and up-classified LLM output.
- `tests/integration/test_jobs_api.py`: storage in PostgreSQL, missing title/company,
  corrections, URLs are never fetched (network access fails the test), validation, manual
  entry, privacy, deletion rules, and the LLM path (grounded, capped, and logged) with its
  fallback.
- `apps/web/src/components/jobs/jobs.test.tsx`: the analysis view's sections, both entry
  forms, and the helpers.
