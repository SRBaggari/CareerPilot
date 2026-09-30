# Claim Verification Engine

An independent component that decides whether each statement in a generated document is
backed by the candidate's own **verified** evidence and stored profile. It doesn't know or
trust how a document was generated.

Code: `apps/api/app/verification/`.

API:
- `POST /api/v1/verification/check` (standalone; nothing stored)
- `POST /api/v1/tailored-resumes/{id}/verify` (re-verify a resume)
- `GET /api/v1/tailored-resumes/{id}/verification-reports` (history)

UI: `/verify` (check any text) and the verification report on `/jobs/[id]/resume`.

## Pipeline

```
Generated document
  -> Claim extraction          every statement, record facts included (extraction.py)
  -> Evidence retrieval        cited evidence + the 3 closest verified items in scope
  -> Evidence comparison       rules against the evidence (compare.py) and against the
                               stored profile (knowledge.py); optional LLM reviewer (llm.py)
  -> Verification              SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONTRADICTED
  -> Approved / Rejected       a claim is approved only if SUPPORTED; a document only if
                               every claim is
```

## Each claim's result

| Field | Meaning |
| --- | --- |
| `claim_text` | The statement as written |
| `claim_type` | Generated claims are `summary`, `skill`, `experience` (a job bullet), `project` (a project bullet), `letter` (a cover letter sentence), `answer` (an application answer sentence) or `statement` (free text). Record facts are `contact`, `employment`, `project_entry`, `education`, `certification`, `achievement` or `coursework`. |
| `evidence_ids` | The evidence the verdict rests on. `cited_evidence_ids` holds what the claim cited. |
| `evidence_source` | `cited`, `retrieved` (the claim didn't cite the evidence that supports it), `profile` (checked against stored records) or `none` |
| `verification_status` | One of the four statuses below |
| `confidence` | 0–1. For rule checks, how much of the claim's content the evidence covers. A hard failure is 0. |
| `reason` | One or two plain sentences, e.g. "Your evidence says 35%, not 60%." |
| `method` | `rule_based` or `llm` (the AI reviewer decided) |

## Statuses

| Status | When |
| --- | --- |
| **SUPPORTED** | The evidence directly states it. Faithful rewording is fine: "Built a RAG-based research assistant." is supported by "Implemented RAG pipeline for document retrieval and question answering." from the Multi-Agent Research Assistant project. |
| **PARTIALLY_SUPPORTED** | The core is backed, but something on top isn't: an extra technology ("…with Docker and Kubernetes"), a qualifier ("scalable", "production"), or wording that adds content ("…and monitored drift"). |
| **UNSUPPORTED** | No verified evidence supports it. This includes claims whose substance rests on something invented: a metric or scale ("serving 10,000 users", "millions"), a named employer, product or certification ("at Google", "AWS Certified"), a bigger role ("Led", "Architected", "Senior"), or content unrelated to the evidence. Citing unconfirmed evidence, another item's evidence or someone else's evidence is also UNSUPPORTED. |
| **CONTRADICTED** | Stored information conflicts with it. |

Examples of CONTRADICTED:
- A different number for the same measure: "Your evidence says 35%, not 60%."
- More years of experience than the work history holds.
- A degree level the education records don't show.
- A different GPA.
- A more senior title at a recorded employer.
- A year outside the dates of the item the claim sits under.
- A record fact (name, employer, title, dates, degree, certification) that differs from the profile.

**Generic self-praise** presented as fact ("passionate", "team player", "strong communication skills", "proven track record") is UNSUPPORTED unless the evidence says it.

**Cover letter and application answer sentences** (`letter`, `answer`) may also be non-factual: a greeting, intent or courtesy. The engine approves one without evidence only if it states nothing about the candidate: no numbers, technologies, qualities, qualifiers or role words, no "I have…" or "my experience…", and no names other than the job and company. Anything else is verified like any claim. See [cover-letters.md](cover-letters.md).

Numbers are compared by what they measure: the few words just before them. A real number
from the evidence can't be attached to something else. "Reduced cloud costs by 30%" is
UNSUPPORTED when the only 30% in the evidence is about triage time.

## Guarantees: nothing is upgraded silently

- **Verified evidence only.** Unconfirmed evidence is never used, even when it says exactly
  the same thing. The reason says so.
- **Right scope.** A bullet can only rest on the evidence of the item it sits under.
- **Vetoes.** Invented numbers and scale, names, role escalation, extra technologies,
  qualifiers, contradictions and record-fact mismatches are hard failures. No later step
  can overrule them.
- **The LLM reviewer** (optional, `CLAIM_VERIFIER`) may always make a verdict stricter. It
  may upgrade a claim to SUPPORTED only when all of these hold:
  - the rules found no hard failure;
  - it cites evidence it was offered;
  - that evidence still passes every rule check;
  - that evidence covers at least 30% of the claim's content.

  An upgrade is recorded as `method: llm` with the reviewer's reason and the rule check
  that preceded it. A reviewer that approves everything cannot approve a hallucination;
  this is tested.
- **Explicit re-citation.** A claim supported by evidence it didn't cite reports
  `evidence_source: retrieved` and says so in the reason. During resume generation, that
  claim then cites the evidence that supports it, and a note on the resume says so.
- **Reports are append-only.** Re-verifying adds a report and a new `claim_verifications`
  row per claim; it never edits old ones. If a resume no longer passes, it becomes
  `verification_failed` and its failing claims are marked `unsupported`. Its content is
  never changed.

## Connected to resume generation

The resume generator doesn't verify its own output. See
[resume-tailoring.md](resume-tailoring.md).

1. The draft is extracted and verified by the engine.
2. Approved claims are kept.
3. Unapproved bullets are rewritten to the evidence they cited, verbatim. Other unapproved
   claims are removed. Every change is audited with the engine's verdict and reason.
4. The final resume is verified again, as a whole. Results for unchanged claims are reused,
   so the LLM isn't called twice.
5. The status is `verified` only if that report is approved; otherwise
   `verification_failed`. The report is stored with trigger `generation`.
6. **Edits** go through the engine too. Anything not SUPPORTED is rejected with a 422 and a
   per-claim reason, e.g. `experience:<id>[0]`. Changing a title, employer, date or name
   is CONTRADICTED; it is not silently corrected.

## Storage

- `verification_reports`: one append-only row per run. It holds the document, the `trigger`
  (generation/edit/manual), the `outcome` (approved/rejected), the `verifier` (`rules` or
  `rules+llm:<model>`), a `report` JSONB with the counts and per-claim results, and the AI
  execution log link.
- `generated_claims`: the resume's statements. They are `verified` or `unsupported`
  according to the engine. Statements removed during generation are kept with status
  `removed` for audit and are never linked to evidence.
- `claim_verifications`: one row per claim per verification run. It records the verdict,
  method, confidence and reason; LLM decisions link their `ai_execution_logs` row.

## Configuration

| Variable | Values | Default |
| --- | --- | --- |
| `CLAIM_VERIFIER` | `auto` (rules + reviewer when `ANTHROPIC_API_KEY` is set), `rules`, `llm` | `auto` |

If the reviewer fails, the rule checks alone decide. The failure is logged and a warning is
added to the report.

## Tests

- `tests/test_verification_compare.py`: the spec examples, faithful rewordings, and more
  than 30 hallucination patterns. Each has its exact expected status: invented metrics,
  scale, dates, borrowed numbers, contradicting numbers, role escalation, invented names
  and certifications, extra technologies, qualifiers, partial content, and unrelated
  content.
- `tests/test_verification_engine.py`:
  - citations: unconfirmed evidence, other candidates' evidence, other items' evidence,
    none at all, and retrieved support;
  - profile contradictions (years, degree, GPA, seniority, dates), plus negative cases such
    as "200 ms" not being read as a degree;
  - record facts;
  - extraction and the report;
  - the reviewer's limits, including an "approve everything" reviewer run against every
    hallucination class.
- `tests/integration/test_verification_api.py`:
  - the check endpoint with all four statuses;
  - another candidate's evidence;
  - validation;
  - a yes-man reviewer (logged, but unable to approve hallucinations);
  - a stricter reviewer;
  - reviewer failure;
  - stored generation reports;
  - re-verification after a profile change (CONTRADICTED, `verification_failed`, content
    unchanged);
  - privacy.
- `tests/integration/test_tailored_resume_api.py`: the generator connected to the engine,
  and edits of record facts rejected as CONTRADICTED.
- `apps/web/src/components/verification/verification.test.tsx` and `resume.test.tsx`: the
  report view, filtering, the checker page, and re-verify.
