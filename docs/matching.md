# Candidate-Job Matching

Compares a candidate's **verified** evidence with a job's structured requirements and
produces an explainable, requirement-by-requirement report. Code: `apps/api/app/matching/`,
API: `POST|GET /api/v1/jobs/{job_id}/match`, UI: `/jobs/[id]/match`.

> The result is **evidence coverage**: how much of the job's stated requirements the
> candidate's verified evidence covers. It is not a hiring probability, and every report says
> so.

## Pipeline

```
Job requirements (REQUIRED / PREFERRED; informational statements are not scored)
  -> 1. Semantic retrieval      top-5 VERIFIED evidence per requirement (pgvector, one embed call)
  -> 2. Structured checks       degree level, years of experience, CGPA, graduation year,
                                unassessable eligibility
  -> 3. Judge                   rules, or an LLM grounded against the retrieved evidence
  -> 4. Explainable scoring     weighted coverage; UNKNOWN reported separately
  -> Report                     per-requirement results + strongest / missing / partial /
                                potentially disqualifying
```

Keyword matching is never the primary method. Retrieval is semantic (embeddings).
Technology names are normalized through a vocabulary ("Retrieval-Augmented Generation" and
"RAG" are one concept; "k8s" is Kubernetes). Then the concept must actually be present in the
cited evidence, which is a grounding check rather than a matching method.

## Statuses

| Status | Meaning |
| --- | --- |
| **MATCHED** | Verified evidence clearly satisfies the requirement, and the evidence is cited |
| **PARTIAL** | Related but incomplete: fewer years than asked, a technology only in coursework, a skill listed but not evidenced, a related but different certification |
| **MISSING** | The evidence shows nothing that satisfies it |
| **UNKNOWN** | Can't be assessed from a profile: work authorization, visas, relocation, soft skills and language fluency with no evidence, a degree level or GPA that isn't recorded |

### Rules by requirement type

| Type | How it is judged |
| --- | --- |
| Technology | MATCHED if retrieved project, work, achievement or profile evidence names it (aliases count). PARTIAL if only coursework or education names it, if it's in the skills list without evidence, or if related evidence doesn't name it. Otherwise MISSING |
| Skill / experience / other statements | Named technologies must all be covered by applied evidence for MATCHED, and some for PARTIAL. Otherwise semantic similarity: high → MATCHED, medium → PARTIAL, low → MISSING (UNKNOWN for soft skills and languages) |
| Experience with a minimum number of years | As above, then **capped at PARTIAL** if the candidate's dated work experience (overlapping jobs merged) is shorter than asked, or has no dates |
| Education | Degree level parsed from the requirement ("Bachelor's or Master's" → bachelor) compared with the candidate's highest level. MATCHED needs evidence on that education entry; without it the result is PARTIAL |
| Certification | Only the **same** credential matches: its distinctive words must correspond. A different certification ("Cloud Practitioner" vs "ML Specialty") is at most PARTIAL |
| Eligibility | Graduation year and minimum CGPA are checked against education entries. GPAs are compared only on the same scale, never converted. Work authorization, visas and similar are **always UNKNOWN** |

## Grounding: no invented evidence

- Retrieval only ever returns the candidate's own **verified** evidence. Unverified evidence
  and other candidates' evidence are excluded, and tests prove it.
- `evidence_ids` can only contain evidence that retrieval returned for that requirement
  (structured checks cite the evidence on the education or certification entry they used).
- MATCHED always cites evidence. PARTIAL without evidence is allowed only when it rests on a
  profile fact (a listed skill or degree), and the explanation says so.
- **LLM judge** (`MATCH_JUDGE=auto` with `ANTHROPIC_API_KEY`): strict JSON-schema output.
  It sees only each requirement's retrieved evidence. Its answer is then checked:
  - IDs it wasn't given are dropped;
  - MATCHED or PARTIAL without valid citations falls back to the rule result;
  - a technology it marks MATCHED must be named in the cited evidence, or it becomes PARTIAL;
  - structured checks (degree, eligibility, GPA, graduation year) can't be overridden, and the
    years cap still applies;
  - failures fall back to the rules, and every call is logged.

## Scoring

- Required requirements weigh **1.0** and preferred ones **0.5**.
- MATCHED earns 1, PARTIAL 0.5, MISSING 0. UNKNOWN is excluded from the denominator and
  counted separately.
- `evidence_coverage` is the weighted share over all scored requirements.
- `required_coverage`, `preferred_coverage`, `skill_coverage` (technologies and skills), and
  `semantic_similarity` (mean best similarity) are also reported.
- The scoring version (`coverage-v1`) is stored, so different versions are never mixed.

## Report

For every requirement: `requirement`, `requirement_type`, `importance`,
`matching_candidate_evidence` (text, source item, resume file, similarity),
`semantic_similarity`, `match_status`, `explanation`, `evidence_ids`, `judge`, and `details`
(the structured-check facts, e.g. `required_years` and `candidate_years`).

It also lists:

- **Strongest matches:** MATCHED, required first, by similarity.
- **Missing skills:** MISSING technologies and skills, without repeating a statement whose
  technologies are already listed.
- **Partial matches.**
- **Potentially disqualifying:** required eligibility conditions that are MISSING or UNKNOWN,
  to check before applying.

A report is flagged `is_stale` when anything matching reads has changed since it was computed.
A content fingerprint covers verified evidence text, skills, education levels, dates and
GPAs, work dates, and certifications. The UI then offers to update it.

## Storage (migration `0007`)

- `job_matches`: one per candidate and job, recomputed in place. Adds `required_coverage`,
  `preferred_coverage`, `matcher_name`, `embedding_model`, `warnings`, and
  `inputs_fingerprint`.
- `requirement_matches`: status, similarity, explanation, judge, and details per requirement.
- `requirement_match_evidence`: cited evidence with its similarity and rank. Deleting the
  evidence removes the link, and the report becomes stale.
- `skill_gaps`: MISSING and PARTIAL requirements, with severity and a recommendation.

## Tests

- `tests/test_matching.py` (no database): the RAG example from the brief; technology, alias,
  coursework, statement, certification, soft-skill and language rules; years caps and merged
  date ranges; degree, graduation-year and CGPA checks; scoring; and LLM grounding (invented
  IDs, unsupported claims, unnamed technologies, decisive checks, caps).
- `tests/integration/test_matching_api.py`: a full profile against a ten-requirement job with
  expected statuses, cited evidence and scores; unverified and other candidates' evidence never
  cited; the LLM judge path; persistence; staleness and recompute; access rules.
- `apps/web/src/components/matching/matching.test.tsx`: report sections, disclaimer, filters,
  the stale banner, and the run-match flow.
