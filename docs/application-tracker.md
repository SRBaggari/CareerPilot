# Application Tracker

This tracks every application from discovery to offer.

- Code: `apps/api/app/applications/`
- API: `/api/v1/applications`
- UI:
  - `/applications`: the dashboard, a board (Kanban) view and a list view;
  - `/applications/[id]`: the detail page and timeline.

**CareerPilot never submits an application.** "Submitted" records that *you* submitted it.
It is only possible after your explicit approval, and the database enforces this, not
just the application code.

## Lifecycle

```
DISCOVERED -> SAVED -> ANALYZED -> APPLICATION_PREPARED -> AWAITING_APPROVAL
    -> (your approval) -> SUBMITTED -> ASSESSMENT -> INTERVIEW -> OFFER
REJECTED / WITHDRAWN at any point
```

| Rule | How it's enforced |
| --- | --- |
| Before submission, move freely between the preparation stages | `allowed_statuses` |
| **Submitted, Assessment, Interview and Offer need `approved_at`** (your approval) | Service, plus the database CHECK `approval_required` |
| They also need `submitted_at`, the date you applied | Service, plus the database CHECK `submitted_has_time` |
| `submitted_at` can't be before `approved_at` | The database CHECK `submitted_after_approval`; the API returns a 422 with the reason |
| Nothing moves back from submitted to preparation | Service (409, with the reason) |
| Rejected and Withdrawn can be reopened to where the application could otherwise be | Service |

A refused move returns 409 with the reason, e.g. "Approve the application first: nothing
counts as submitted without your approval".

### Approval

Approval is explicit and bound to the exact content you reviewed; see
[human-approval.md](human-approval.md). In short: mark the application ready for review,
open the review (job, company, resume, cover letter, answers, personal information,
verification results, missing or uncertain fields), then approve that version with
`{content_hash, confirm: true}`. Approving:

- records you as the reviewer, the time, and the approved content version;
- moves the application to "Awaiting approval";
- marks the attached resume and cover letter **approved**, so they're locked from further
  edits and kept when you regenerate.

You then submit the application yourself and record it ("Submitted", with the date you
applied), or let browser assistance submit it. Either way, submission is refused if the
content changed after approval or anything is missing or unverified.

## What is tracked

| Field | Source |
| --- | --- |
| Company, position, location | The job |
| Job URL | `application_url`, or the job's URL by default |
| Date discovered | `discovered_at`: when the job was added, or when it was recommended |
| Date applied | `submitted_at` |
| Resume used, cover letter used | The attached versions. The latest are attached when tracking starts, and you can change them until you approve. Attached versions survive regeneration and can't be deleted, and a newer version is flagged. |
| Application answers | The job's answers, with how many are approved |
| Status, notes | Editable |
| Interview dates | `interviews`: type, time, duration, location or meeting link, status |
| Follow-up dates | `follow_ups`: subject, due date, channel, status |

## Timeline

The timeline is newest first and merges these events:
- when tracking started and when the job was discovered;
- every status change, with its note and whether it was automatic;
- your approval;
- documents created and answers approved;
- interviews;
- follow-ups done or due.

Scheduled items are marked **upcoming**.

## Follow-up reminders

Some reminders are created for you; CareerPilot never sends anything on your behalf:
- **after you record the submission**, "Check in on your application", due 7 days later
  (unless a follow-up is already pending);
- **after an interview is marked completed**, "Send a thank-you note", due the next day.

Adding an interview to a submitted application, or one in assessment, moves it to
"Interview". The timeline shows this move as automatic.

Reminders can be added, rescheduled, marked done or skipped, and deleted. The dashboard
lists every follow-up that is overdue or due within 7 days, across all applications, with
Done and Skip buttons.

## Dashboard, board and list

- **Dashboard:**
  - counts: tracked, active, submitted or later, and offers;
  - follow-up reminders;
  - interviews in the next 14 days.
- **Board:** one column per status. Each card has a "Move to" control, and a refused move
  shows the reason.
- **List:** a table with the dates discovered and applied, and the next interview or
  follow-up.
- **Filtering and search:**
  - status (several at once);
  - search across company, position, location and notes;
  - "Only with a follow-up due this week";
  - sort by recently updated, company, date discovered or date applied.

Tracking starts from a job's page ("Track application") or from a recommendation ("Start
Application"). Each job is tracked once; a second request returns the existing
application.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/applications?status=&q=&follow_up_due=&sort=` | List, filter and search |
| GET | `/applications/dashboard` | Counts, reminders, upcoming interviews, recent applications |
| POST | `/applications` | Track a job: 201 when new, 200 when it already exists |
| GET, PATCH, DELETE | `/applications/{id}` | Detail; edit notes, URL and attached documents; delete |
| POST | `/applications/{id}/status` | Move it; `submitted_on` is the date you applied |
| GET | `/applications/{id}/review` | Everything to review before submitting (never approves) |
| POST | `/applications/{id}/review/request`, `/approve`, `/reject` | Approval decisions ([human-approval.md](human-approval.md)) |
| POST, PATCH, DELETE | `/applications/{id}/interviews[/{interview_id}]` | Interviews |
| POST, PATCH, DELETE | `/applications/{id}/follow-ups[/{follow_up_id}]` | Follow-ups |

Migration `0014` replaced the older status set with this lifecycle. It mapped existing
rows (e.g. `ready_for_review` became `awaiting_approval`), updated the approval CHECKs, and
added `discovered_at`.

## Tests

- `tests/test_application_tracker.py`:
  - allowed moves: preparation, approval unlocking submission, no way back, reopening;
  - every approval blocker.
- `tests/integration/test_applications_api.py`:
  - tracking a job, once;
  - the full lifecycle: submission refused without approval, the approval readiness
    check, submitted with the date applied, the automatic check-in reminder, no way back,
    an interview moving the status, the thank-you reminder, completing a follow-up,
    offer, and the timeline;
  - blockers from unapproved answers and a missing resume;
  - interviews needing a submission, and the date applied not preceding approval;
  - attached documents surviving regeneration and being protected;
  - filters, search and the dashboard;
  - follow-up rescheduling, skipping and deletion;
  - deletion freeing the job;
  - privacy.
- `apps/web/src/components/applications/applications.test.tsx`:
  - query encoding;
  - the dashboard, reminders and board columns;
  - filters, search and the list view;
  - a refused move;
  - completing a reminder;
  - the detail page's facts, documents, readiness and timeline;
  - approve and submit;
  - approval blockers;
  - adding interviews, follow-ups and notes.
