# Resume Ingestion

Candidates can upload their existing resume to pre-fill the master profile. Everything read
from the resume is **untrusted** until the candidate reviews it.

```
Upload ─► Text extraction ─► Section detection ─► Structured extraction ─► Grounding
       ─► Suggestions (+ highlights, source excerpt) ─► Review: Accept / Edit / Reject
       ─► Master profile + one evidence row per claim
```

Code: `apps/api/app/resumes/`, the review API in `app/profiles/suggestions.py`, and the UI in
`apps/web/src/components/profile/ResumeCard.tsx` and `ExtractedInfoReview.tsx`.

## 1. Upload (`POST /api/v1/resumes`, multipart `file`)

- **Formats:** PDF and DOCX, identified from the file **content** (magic bytes). The extension
  must match. Legacy `.doc`, images, and other files are rejected with a clear message.
- **Limits:** 5 MB (`MAX_RESUME_BYTES`), 20 PDF pages, 50 MB uncompressed DOCX (zip-bomb
  guard), 100k characters of text.
- **Duplicates:** the same file (by SHA-256) can't be uploaded twice for one profile (409).
- **Storage:** `STORAGE_DIR/resumes/<profile-id>/<random>.<ext>`. The user's filename is never
  used as a path. If processing fails unexpectedly, the file and the database row are both
  removed.
- The first upload becomes the **primary** resume.
- A profile must exist first.

## 2. Text extraction (`extraction.py`)

- PDF uses `pypdf`. Encrypted PDFs are tried with an empty password and otherwise rejected.
- DOCX uses `python-docx`. Paragraphs and tables are read in document order, and Word list
  paragraphs are marked as bullets.
- Text is normalized: Unicode NFKC, one canonical bullet `•`, and collapsed whitespace.
- If no text is found (a scanned or image-only resume), the upload is saved with
  `parse_status = failed` and an explanation. OCR is not supported.

## 3. Section detection (`sections.py`)

Headings are recognized from a vocabulary of common names: Education, Experience/Internships,
Projects, Skills, Certifications, Achievements/Awards, Coursework, Summary. Inline headings
such as `Relevant Coursework: A, B` are recognized too. Languages, Interests, References and
similar sections are detected but not imported. Text before the first heading is the header
(name and contact details).

## 4. Structured extraction

The parser is chosen by `RESUME_PARSER`:

| Value | Behaviour |
| --- | --- |
| `auto` (default) | Claude when `ANTHROPIC_API_KEY` is set, otherwise rule-based |
| `heuristic` | Always rule-based (offline, deterministic) |
| `llm` | Claude. If it isn't configured, falls back to rule-based with a warning |

- **Rule-based** (`heuristic.py`): splits sections into entries (heading lines + bullets, with
  wrapped bullet lines rejoined), recognizes dates and ranges (`May 2023 – Present`, `06/2022`,
  `2020`), degrees, GPA/CGPA/percentages, roles, companies, URLs, and labelled skill lines
  (`Frameworks: …`). If a required field (e.g. a company) can't be found, **the entry is
  skipped with a warning**. Values are never guessed.
- **Claude** (`llm_parser.py`): one call through the `LLMProvider` abstraction
  (`app/ai/provider.py`). It uses `claude-opus-5-5` with JSON-schema structured outputs and
  `effort: medium`. The prompt requires verbatim copying and nulls for missing data. Refusals,
  truncation, malformed JSON, and API errors raise `LLMError`, and the pipeline then falls back
  to the rule-based parser. Server-side refusal fallbacks (`fallbacks: "default"`) are enabled.
  Every call is recorded in `ai_execution_logs` (tokens, latency, status). Resume content is
  **not** stored in the log.
- **Dates:** a year alone is stored as January 1 (December 1 for the end of a range). The
  reviewer sees the date and can edit it.

## 5. Grounding (`grounding.py`)

Every value from either parser must be **found in the extracted resume text**. The comparison
normalizes case, whitespace, dashes, and quotes. URLs are compared without scheme or `www`.
Dates are checked by year, and numbers by their digits. Values that aren't found are dropped:

- a highlight, a skill, or an optional field is removed;
- an entry whose required field isn't found is removed entirely;
- the resume's `parse_warnings` says how many values were discarded.

This is the main defense against a model inventing facts. For example, an extra bullet
"Promoted to team lead" that isn't in the resume never reaches the review queue.

## 6. Suggestions

Grounded results become `profile_suggestions` rows (`source = resume_extraction`,
`resume_id`, `source_excerpt`):

| Extracted | Suggestion |
| --- | --- |
| Name, email, phone, location, links, summary | One `personal_info` update. It only fills fields the profile **doesn't already have**; a different name is proposed, never overwritten |
| Education, experience, projects, certifications, achievements, coursework | One `create` per entry, validated with the same schema as manual edits, with `highlights` (one per bullet) |
| Skills | One `create` per skill, with its source line (e.g. `Languages: Python, Java`) |

Suggestions are never created for information already in the profile, or already suggested
before (pending, accepted, or rejected). So re-uploading a resume doesn't bring back items you
rejected, and an item you edited on acceptance isn't offered again.

## 7. Review: Accept / Edit / Reject

The **Extracted information** panel is separate from the profile, labelled "not part of your
profile", and grouped by section. Each item shows its verbatim resume text.

- **Accept** → `POST /api/v1/profile/suggestions/{id}/accept`
- **Edit** → the same endpoint with `{"proposed_data": {...}}`. The edited data is validated
  exactly like a manual edit.
- **Reject** → `POST /api/v1/profile/suggestions/{id}/reject`

Acceptance is **atomic**: the profile change, every evidence row, and the suggestion's status
commit together or not at all. `proposed_data` keeps what was extracted, and `accepted_data`
records what the candidate confirmed.

## 8. Evidence for every claim

Accepting an entry creates **one `candidate_evidence` row per claim**:

- each highlight (bullet) of a project or job becomes its own evidence row;
- an entry without bullets (e.g. a certification line) uses its verbatim source text;
- a skill is linked (`candidate_evidence_skills`) to profile-level evidence holding the resume
  line it came from.

All such evidence has `origin = resume_extracted` (the UI shows "From your resume · approved by
you"), a `source_resume_id`, and a `confirmed_at` set by the acceptance. Contact details and
the name are identity data, not claims, so no evidence is created for them.

## 9. Managing uploads

- `GET /api/v1/resumes` lists uploads with status, warnings, and pending/total counts.
- `GET /api/v1/resumes/{id}` also returns the extracted text ("View extracted text" in the UI).
- `DELETE /api/v1/resumes/{id}` removes the file, the row, and its **pending** suggestions.
  Accepted information stays in the profile; its evidence keeps `origin = resume_extracted`
  with `source_resume_id = NULL`.

## Limitations

- No OCR: scanned resumes must be converted to text PDFs or DOCX.
- The rule-based parser is tuned for common single-column layouts. Unusual layouts produce
  fewer suggestions and more warnings, never invented ones.
- Parsing runs during the upload request. A background queue can be added if large volumes or
  slow LLM calls require it.
