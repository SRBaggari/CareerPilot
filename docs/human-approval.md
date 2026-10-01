# Human-in-the-loop approval

Every application has an **approval state**, separate from its lifecycle status. Nothing is
submitted, by the candidate recording it or by browser assistance, unless the candidate
explicitly approved exactly the content being submitted.

```
DRAFT ──(mark ready for review)──► READY_FOR_REVIEW ──(explicit approval)──► APPROVED ──► SUBMITTED
                                      │      ▲                                  │
                                      │      └──── content changed (automatic) ─┤
                                      └──(reject)──► REJECTED ◄──(withdraw)─────┘
REJECTED ──(mark ready for review)──► READY_FOR_REVIEW
```

UI: **Applications → an application → "Review everything and approve"**
(`/applications/[id]/review`).

## What the candidate sees before approving

The review package (`GET /api/v1/applications/{id}/review`) contains:

1. **Job**: title, location, and where to apply.
2. **Company**.
3. **Resume**: the attached version, rendered as it will be submitted.
4. **Cover letter**: the attached version (optional).
5. **Application answers**: every question with its answer and approval status.
6. **Personal information**: name, email (and whether it's the profile's contact email or
   the account email), phone, location, LinkedIn.
7. **Evidence verification results** for each document and answer: verified or not, the
   latest verification report (outcome, verifier, when, counts by verdict), the number of
   verified claims, and every unverified claim with its verdict and reason.
8. **Missing or uncertain fields**: *blockers* (prevent approval and submission) and
   *warnings* (shown, don't block).

It also includes the `content_hash` of what is shown, the current approval (reviewer,
time, version), the approval history, whether it can be approved or submitted and why
not, and the audit log.

**Opening the review is never approval.** It is recorded (`review_opened`) and changes no
state; a test opens it repeatedly and checks nothing was approved.

## Approving

`POST /api/v1/applications/{id}/approve` with `{"content_hash": "<from the review>",
"confirm": true}`, optionally a `note`. It is refused unless:

- the application is `ready_for_review` (not straight from draft);
- `confirm` is `true` (422 otherwise);
- `content_hash` matches the current content (409 "changed since you opened the review");
- there are no blockers (409, listing them, audited as `approval_blocked`).

The reviewer is the signed-in user. It can't be supplied: unknown body fields are rejected
(422). In the UI, the Approve button stays disabled until the confirmation checkbox is
ticked.

### What is stored

| Where | What |
| --- | --- |
| `applications.approval_state` | The current state |
| `applications.approved_by_id`, `approved_at`, `approved_content_hash` | Reviewer, approval timestamp, approved content version |
| `applications.submitted_at` | Submission timestamp |
| `application_approvals` (append-only) | Every approval and rejection: `version` (1, 2, … per application), `decision`, `reviewer_id`, `content_hash`, the **full content snapshot** that was reviewed, `note` |
| `application_audit_events` (append-only) | `review_requested`, `review_opened`, `approved`, `approval_blocked`, `rejected`, `approval_invalidated`, `submission_blocked`, `submitted`; with actor (user/system/automation), user, details |

## The content hash

The approved content version is the SHA-256 of a canonical JSON snapshot of the job
(title, company, location), where to apply, the personal information, the resume and cover
letter (id, version, full content), and every written answer (id, question, text).
Statuses are not part of it, so approving (which marks the documents approved) doesn't
change it.

## Submission gate

`ensure_can_submit` runs before every submission: recording it (`POST
/applications/{id}/status` to `submitted`) and browser-assisted submission (before the
browser starts). Submission is refused (409, audited as `submission_blocked`) if:

| Rule | Check |
| --- | --- |
| The candidate has not approved | `approval_state` is not `approved` |
| The application changed after approval | the current content hash differs from `approved_content_hash` |
| Required fields are missing | any blocker: no resume, no name, application not prepared |
| Claims are unverified | any document or answer not fully verified: its status, its latest verification report, or any stored claim not verified |

When the content changed, the approval is **withdrawn automatically** (back to
`ready_for_review`, `approval_invalidated` audited with which sections changed). This also
happens when the review is opened, when browser assistance starts, and immediately when the
application's URL is edited. The candidate sees the new version and approves it again,
which creates approval version 2.

On submission the state becomes `submitted`, and a `submitted` audit event records the
approved hash (and, for browser assistance, the site's confirmation reference).

## Database guarantees

| CHECK on `applications` | Meaning |
| --- | --- |
| `approval_recorded` | `approved` requires `approved_at`, `approved_by_id` and `approved_content_hash` |
| `unapproved_has_no_approval` | `draft`, `ready_for_review` and `rejected` have no `approved_at` and no `submitted_at` |
| `submitted_state_matches` | `submitted_at` is set exactly when the state is `submitted` |
| `approval_required`, `submitted_after_approval` (existing) | Submitted stages need `approved_at`, and submission can't precede approval |

Migration 0016 withdraws approvals that predate this system (back to `ready_for_review`)
so the candidate approves the current content explicitly. Submitted applications keep
their submission.

## Security and authorization

- Every endpoint resolves the application through the signed-in candidate's profile:
  another user gets 404 for the review, request, approve, reject and status endpoints, and
  their attempts change nothing.
- Without an identity (e.g. production before real authentication is configured), every
  endpoint returns 401.
- The reviewer comes from the session, never from the request.
- Tests: `tests/integration/test_approval_api.py` (review package, explicit approval,
  state transitions, changes after approval, unverified claims, missing fields,
  rejection and withdrawal, cross-user access, unauthenticated access, database
  constraints) and `test_assisted_applications_api.py` (a change after approval stops
  browser-assisted submission before anything is sent).

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/applications/{id}/review` | The review package. Recorded; never approves |
| POST | `/api/v1/applications/{id}/review/request` | Draft or rejected → ready for review |
| POST | `/api/v1/applications/{id}/approve` | `{content_hash, confirm: true, note?}`: explicit approval |
| POST | `/api/v1/applications/{id}/reject` | `{note?}`: reject, or withdraw an approval |
