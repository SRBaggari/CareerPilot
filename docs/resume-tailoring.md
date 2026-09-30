# Resume Tailoring

Generates a resume for one job from the candidate's **verified** evidence. The resume
emphasizes the most relevant legitimate information and invents nothing. Code:
`apps/api/app/documents/resume/`. API: `/api/v1/jobs/{job_id}/tailored-resumes` and
`/api/v1/tailored-resumes/{id}`. UI: `/jobs/[id]/resume`.

## The two kinds of content

| Kind | Examples | Where it comes from | Can the generator change it? |
| --- | --- | --- | --- |
| **Record facts** | name, contact, employers, job titles, project titles, degrees, certifications, **all dates** | Copied from the profile records by ID | No. It can only choose and order records by ID. |
| **Claims** | summary sentences, bullets, listed skills | Generated wording, each citing evidence IDs | Yes, but every claim is verified before it is kept |

Record facts are never generated, so employment, projects, certifications and dates can't
be invented or altered. The [claim verification engine](claim-verification.md) checks
them anyway, against the profile. An edit that changes a title, employer, date or name is
rejected as CONTRADICTED, and an unknown record ID as UNSUPPORTED. Neither is silently
corrected.

## Claim-first pipeline

```
1. Evidence retrieval   verified evidence only; relevance = cosine(job, evidence)
                        + a boost for evidence the job match report cites
2. Candidate claims     each record's evidence, ranked; skills backed by evidence
3. Resume generation    rules (verbatim evidence) or an LLM (wording + selection by ID)
4. Claim extraction     every statement, including record facts (verification engine)
5. Claim verification   the independent engine (claim-verification.md)
6. Final resume         approved claims kept; others rewritten or removed; then the whole
                        resume is verified again, and that report decides its status
```

If the job match report is missing or stale, it is computed first; its cited evidence
feeds step 1.

### What the generator may choose

- **Skills**: only skills on the candidate's profile, and only those the verified
  evidence mentions. Required job technologies come first. Listed skills without
  evidence are left out and named in the notes.
- **Projects**: only projects with verified evidence, most relevant first, at most 4.
- **Experience**: every job, with the current role first and then by date. Bullets are
  that job's own evidence.
- **Coursework and achievements**: the most relevant, chosen by ID.
- **Bullets**: may cite only the evidence of the record they sit under.
- **Summary**: optional. At most two sentences, each citing evidence.

The LLM output is structured and strict. Any ID it returns that isn't the candidate's own
is dropped before verification and recorded as rejected: an unknown project, another
record's evidence, another candidate's evidence, or unverified evidence.

### Verification

Generation is connected to the [claim verification engine](claim-verification.md), which
runs independently of the generator. Each claim gets one of four statuses: SUPPORTED,
PARTIALLY_SUPPORTED, UNSUPPORTED or CONTRADICTED. Only SUPPORTED is approved.

The engine catches:
- invented or borrowed numbers and scale;
- a different number than the evidence states (CONTRADICTED);
- invented names and certifications;
- role escalation;
- extra technologies and qualifiers;
- conflicts with the profile: years of experience, degree, GPA, seniority, dates;
- record-fact mismatches.

### Rewrite or reject

| Claim | When unsupported |
| --- | --- |
| Bullet that cited its item's own verified evidence | **Rewritten** to that evidence verbatim |
| Bullet without valid cited evidence | **Removed** |
| Summary sentence, skill | **Removed** |

The rewritten resume is verified again as a whole. It is `verified` only if that report
approves every claim; otherwise it is `verification_failed`.

Every rewrite and removal is stored and shown in the UI with the engine's verdict and
reason:
- as a `generated_claims` row with status `removed`;
- plus a `claim_verifications` row.

Removed claims are **not** linked to evidence, so they never make evidence look "cited".
The final report is stored in `verification_reports`. **Re-verify**
(`POST /api/v1/tailored-resumes/{id}/verify`) checks a stored resume against the current
evidence and profile. It reports problems and never fixes them.

## Storage

Tailored resumes are stored separately from the uploaded master resumes:
- `tailored_resumes.content` holds the resume JSON;
- `generator_name` records whether rules or an LLM wrote it;
- `notes` holds skills left out and fallbacks;
- `ai_execution_log_id` links the LLM call, when there was one.

Kept claims are stored as `generated_claims` with status `verified`, linked to their
evidence via `generated_claim_evidence`. Cited evidence therefore can't be deleted (409)
until the resume is.

Regenerating creates version *n + 1* and removes earlier unapproved versions for the same
job, which also releases their evidence. A job with a tailored resume can't be deleted
until the resume is deleted.

## Editing

`PUT /api/v1/tailored-resumes/{id}` accepts the edited content. The candidate can:
- reword statements;
- reorder bullets and projects;
- remove statements, skills and projects.

Every claim is re-verified. Saving is all or nothing. A 422 lists each unsupported
statement under its key, such as `experience:<record_id>[0]`, with the reason. For
example: *"Contains numbers or metrics not in the evidence: 47."* To claim something new,
the candidate adds it as evidence on their profile first.

## Downloads

`GET /api/v1/tailored-resumes/{id}/download?format=pdf|docx` returns an attachment.

Both formats render from one layout:
- DOCX via python-docx;
- PDF via fpdf2 with its core fonts, which only cover Latin-1, so typography is
  normalized: ₹ becomes "INR".

Nothing is added at render time; the files contain exactly the previewed text.

## Configuration

| Variable | Values | Default |
| --- | --- | --- |
| `RESUME_GENERATOR` | `auto` (LLM when `ANTHROPIC_API_KEY` is set), `rules`, `llm` | `auto` |

If the LLM fails, the resume falls back to rules. The failure is logged in
`ai_execution_logs` and noted on the resume.

## Tests

The hallucination tests cover three levels:

- `tests/test_verification_compare.py` and `tests/test_verification_engine.py` (see
  [claim-verification.md](claim-verification.md)) test the verifier against invented or changed metrics,
  dates, vague quantities, technologies, exaggerated roles, invented names, unrelated
  content, missing or wrong citations, and unsupported skills. Faithful rewordings must
  still pass.
- `tests/integration/test_tailored_resume_api.py` uses a fake "hallucinating" model. It
  returns:
  - a fabricated certification and employer;
  - another candidate's evidence;
  - skills not on the profile, or without evidence;
  - an unknown project;
  - an inflated metric;
  - "spearheaded … used by millions";
  - a technology cited from another record's evidence.

  The test asserts that none of these reach the final resume, the PDF or the DOCX, and
  that each is audited with a reason. Other integration tests cover:
  - record facts and dates;
  - unverified evidence;
  - versions;
  - edits (accepted, and rejected with reasons);
  - evidence protection;
  - privacy.
- `apps/web/src/components/resume/resume.test.tsx` tests the preview, the verification
  panel, sources, confirmed regeneration, editing, and inline 422 errors.
