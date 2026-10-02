# Cover Letters

A concise, professional cover letter for one job. It is built from the candidate's
**verified** evidence and checked sentence by sentence by the
[claim verification engine](claim-verification.md).

- Code: `apps/api/app/documents/cover_letter/`
- API: `/api/v1/jobs/{job_id}/cover-letters` and `/api/v1/cover-letters/{id}`
- UI: `/jobs/[id]/cover-letter`, linked from the job, match and resume pages

## Inputs

The inputs are the candidate profile, the verified evidence, the analyzed job and the job
match report. A missing or stale match report is computed first.

The match report decides what to lead with. Evidence cited for matched requirements comes
first, required requirements before preferred ones, then evidence by relevance to the job.
Required requirements the evidence doesn't meet are **not** mentioned, and the candidate
is told which ones were left out.

## The letter

| Part | Source |
| --- | --- |
| Job title and company | The analyzed job posting |
| Signature (name, email, phone, location) | The profile, always as it currently is |
| Greeting, sentences, closing | Generated and verified |

Each sentence is either:
- **factual**: it says something about the candidate, cites the evidence it rests on, and
  must be SUPPORTED; or
- **non-factual**: a greeting, a statement of intent or interest, or a courtesy ("I would
  welcome the opportunity to discuss the ML Engineer role with you.").

The engine decides which kind a sentence is; the generator's label isn't trusted.

A sentence counts as non-factual only if all of these hold:
- it opens like one;
- it contains no numbers, technologies or generic qualities ("passionate", "team player",
  "strong communication skills");
- it contains no qualifiers, scale or role words, except those in the job's own title;
- it says nothing about the candidate's past or abilities ("I have…", "my experience…");
- it names nothing other than the company and job title;
- every word is courtesy or intent vocabulary ("I would welcome the opportunity to
  discuss…", "Thank you for your time"), the job title or the company. This is an
  allow-list, so "I'd bring hands-on experience…", "a candidate who has shipped…" and
  "my team won…" are factual claims.

A salutation or sign-off may contain only those names and standard salutation words.
Anything else is a factual claim and needs evidence.

Generic self-praise is UNSUPPORTED unless the evidence itself says it. So is an invented
achievement, experience, employer or metric. A number that differs from the evidence is
CONTRADICTED. So is a claim that conflicts with the profile, such as more years of
experience or a more senior title.

## Generators

- **Rules (the default without an API key):** the letter has four parts:
  - an opening statement of intent;
  - the strongest matched work evidence, framed in the first person without adding facts
    (for example "As a Machine Learning Intern at Acme Analytics, I deployed ML models with
    Docker on AWS.");
  - project evidence, plus "I have worked with …" for required skills the evidence shows;
  - a closing statement of intent and thanks.
- **LLM (`COVER_LETTER_GENERATOR`):** Claude writes 3–4 short paragraphs of at most 300
  words, with strict structured output. Evidence IDs other than the candidate's own
  verified ones are dropped. If the call fails, the rule-based letter is used; the failure
  is logged and noted.

## Pipeline: verify, regenerate or remove, verify again

1. **Verify the draft** with the engine: every sentence, plus the greeting, closing and
   signature.
2. **Greeting or closing that makes claims:** replaced with a plain one ("Dear Northwind
   Hiring Team,", "Sincerely,").
3. **Regenerate failed sentences.** With the LLM, the failed sentences are sent back with
   the reason and the evidence they may use. The LLM returns a rewrite or drops the
   sentence. Without it, a sentence is re-framed from the first evidence it cited. A
   regeneration that repeats an existing sentence is dropped.
4. **Verify again.** Regenerations that still fail are removed, along with sentences that
   had nothing to regenerate from.
5. **Re-cite.** Kept sentences cite the evidence that supports them. If that differs from
   what was generated, a note says so.
6. **Final check.** The whole letter is verified independently. The letter is `verified`
   only if that report is approved; otherwise it is `verification_failed`. Unchanged
   results are reused, so the LLM reviewer isn't called twice.

Every regeneration and removal is audited with the engine's verdict and reason, shown
under "Changes made during generation". Removed sentences are stored as
`generated_claims` with status `removed` and are never linked to evidence.

## Preview, edit, save, regenerate, download

- **Preview:** the letter as it will be downloaded. Optionally, the evidence behind each
  sentence is shown. The verification report and the generation changes appear alongside.
- **Edit and save:** `PUT /api/v1/cover-letters/{id}` with `{greeting, paragraphs[],
  closing}` as plain text.
  - Paragraphs are split into sentences. Unchanged sentences keep their citations; new or
    changed ones start with none, and the engine looks for evidence that supports them.
  - Anything not SUPPORTED rejects the whole save with a 422, with reasons keyed by
    `greeting`, `closing` or `paragraphs[i]`, and nothing is saved.
  - Job title, company and signature aren't editable.
- **Regenerate:** `POST /api/v1/jobs/{job_id}/cover-letters`. This makes version *n + 1*
  and replaces earlier unapproved versions for the job, which also releases their
  evidence. The UI asks for confirmation first.
- **Download:** `GET /api/v1/cover-letters/{id}/download?format=pdf|docx`. The file
  contains the header, date, a "Re:" line with the job title and company, the greeting,
  the paragraphs, the closing and the name. It holds exactly the previewed text.
- **Re-verify:** `POST /api/v1/cover-letters/{id}/verify`. This checks the letter against
  the current evidence and profile and adds a report. It never edits the letter.

Cited evidence can't be deleted (409), and the job can't be deleted, until the letter is
deleted (`DELETE /api/v1/cover-letters/{id}`).

## Tests

- `tests/test_cover_letter.py`:
  - what counts as non-factual: greetings, intent, courtesy, and salutations that smuggle
    in claims;
  - generic self-praise;
  - letter sentences through the engine: supported, invented metric, invented award,
    traits, contradicted title and years;
  - first-person framing;
  - the rule generator, and ID filtering in the LLM output.
- `tests/integration/test_cover_letter_api.py`:
  - a grounded, concise letter;
  - an empty profile;
  - a hallucinating model whose letter contains an inflated greeting, generic traits, a
    contradicted metric, an invented award, and a foreign employer cited with another
    candidate's evidence. Each is regenerated or removed, audited and logged;
  - model failure;
  - edits (accepted, and rejected with reasons);
  - versions, downloads, re-verification after a profile change;
  - evidence and job protection, and privacy.
- `apps/web/src/components/coverLetter/coverLetter.test.tsx`: generate, preview, report,
  changes, sources, confirmed regeneration, save, inline 422 errors, and re-verify.
