# Application Answers

Answers to the questions job applications ask ("Why are you interested in this role?",
"Describe your experience with Python."). Each answer is written only from the candidate's
**verified** evidence and checked sentence by sentence by the
[claim verification engine](claim-verification.md).

- Code: `apps/api/app/documents/answers/`
- API: `/api/v1/jobs/{job_id}/application-answers` and `/api/v1/application-answers/{id}`
- UI: `/jobs/[id]/questions`, linked from the job and match pages

Nothing is ever submitted to an employer. Approval is the candidate's own sign-off.

## For each question

1. **Understand it.** A deterministic classifier (`questions.py`) reads the question as one
   of seven types. The UI shows how each question was read.

   | Type | Example | Answered with |
   | --- | --- | --- |
   | Motivation | "Why are you interested in this role?" | A statement of interest, 1–2 sentences of evidence behind matched job requirements, and a closing |
   | Fit | "Why should we hire you?" | Evidence behind the job's matched requirements (required first) |
   | Project | "Describe a relevant project." | One project: the one whose evidence best fits the question |
   | Skill | "Describe your experience with Python." | Only evidence that names the skill ("focus"). If none does, **no answer is written**, and the candidate is told why. |
   | Experience | "Tell us about your background." | The evidence most relevant to the question |
   | Situation | "Describe a time you…" | Only what the evidence shows, with a note to add the story in their own words |
   | General | anything else | The evidence most relevant to the question |

2. **Retrieve evidence.** A semantic search over verified evidence only, using the
   question, its focus and the job title. The job's match report also ranks evidence for
   fit and motivation questions.
3. **Generate.**
   - **Rules** (the default without an API key): the evidence, framed in the first person
     without adding facts. For example, "In my Multi-Agent Research Assistant project, I
     built data pipelines in Python and SQL." A second sentence about the same item reads
     "I also …".
   - **LLM** (`ANSWER_GENERATOR`): Claude answers from the retrieved evidence in 2–5
     sentences, with strict structured output. Evidence IDs other than the candidate's own
     verified ones are dropped. If the call fails, the rule-based answer is used.
   - An optional word limit (20–1000) drops sentences from the end, keeping at least one.
4. **Verify.** Every sentence goes through the engine.
   - A sentence of pure interest or intent ("I am interested in the ML Engineer role at
     Northwind.") is approved only if it claims nothing: no skills, numbers, generic
     qualities, "I have…" or "my experience…", and no names other than the job and
     company.
   - Failed sentences are regenerated from their own evidence (LLM revision or rule-based
     reframing) and verified again. Whatever still fails is removed.
   - The final answer is verified as a whole. It is `verified` if every sentence is
     supported, `verification_failed` if any sentence isn't, and `draft` if the evidence
     can't answer the question.
5. **Show the evidence used.** Every evidence item the answer cites, verbatim, with the
   item it belongs to. The full verification report and the generation changes are
   available on each card.
6. **Edit.** `PUT /api/v1/application-answers/{id}` with `{answer}` as plain text.
   - Unchanged sentences keep their citations; new or changed ones start with none, and
     the engine looks for evidence that supports them.
   - Anything not SUPPORTED rejects the save with a 422 and a reason per sentence, and
     nothing is saved.
   - Editing an approved answer withdraws the approval.

## Buttons

| Button | What happens |
| --- | --- |
| **Edit** | Edit the answer as text; saving verifies it first |
| **Regenerate** | `POST …/{id}/regenerate`: answer again from current evidence; this withdraws any approval (the UI asks first if the answer was approved) |
| **Approve** | `POST …/{id}/approve`: verifies the answer **once more** against the current evidence and profile. If it passes, the status becomes `approved` with `approved_at`. If not, it becomes `verification_failed` and approval is refused (409); the answer isn't changed. |
| Delete | Removes the question and its answer |

## Storage

- `application_answers`: the question, `question_type`, `focus`, `position`, `max_words`,
  the answer as `{"sentences": [{text, evidence_ids, claim_id}]}`, `status`,
  `approved_at` (required when approved), `generator_name`, `notes`, and
  `ai_execution_log_id`. Deleting the job deletes its answers.
- Sentences are `generated_claims` linked by `application_answer_id`, with a
  `claim_verifications` row per check. Removed originals are kept as `removed` for audit.
  Cited evidence can't be deleted (409) while an answer cites it.
- Each check is a `verification_reports` row, with trigger generation, edit or approval.
- AI calls are logged in `ai_execution_logs` with operation `application_answer`.

## Tests

- `tests/test_application_answers.py`:
  - question understanding (15 phrasings);
  - rule answers per type: skill answers use only evidence naming the skill; a skill with
    no evidence gets no answer; a project answer describes one project;
  - motivation structure, fit, and word limits.
- `tests/integration/test_application_answers_api.py`:
  - the four example questions end to end, each grounded, with its evidence used;
  - Kubernetes with no evidence gives no answer and can't be approved;
  - word limits and validation;
  - a hallucinating model: an inflated metric is regenerated, and generic praise and an
    invented employer, role and years are removed;
  - edit, approve, then edit again (approval withdrawn);
  - rejected edits;
  - approval refused after a profile change, then regenerate and approve;
  - ordering, evidence protection, job deletion and privacy.
- `apps/web/src/components/answers/answers.test.tsx`:
  - the question card (question, answer, evidence used, status, buttons);
  - generating several answers;
  - an edit rejected with a reason, then saved;
  - approve, refused approval, and regenerate.
