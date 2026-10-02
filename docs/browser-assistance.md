# Browser-assisted applications

CareerPilot can fill an approved application on a **supported** application site, then
pause and show the candidate exactly what would be sent. It submits only after the
candidate explicitly confirms that exact review. Everything it does is recorded in an
audit log.

```
Select an approved application
  → open the supported application page (stop at any access control)
  → detect the supported fields
  → fill personal information, attach the approved resume and cover letter,
    fill the approved answers
  → read the form back → PAUSE: Application Review (nothing submitted)
  → candidate ticks the confirmation and presses Submit
  → refill, check the form still matches the confirmed review → submit
```

UI: **Applications → an approved application → "Fill it in on the site for my review"**
(`/applications/[id]/assist`).

## Safety rules

| Rule | How it is enforced |
| --- | --- |
| Never submit without explicit approval | The application must be approved by the candidate and unchanged since ([human-approval.md](human-approval.md)); the submission gate runs before the browser starts. Submission needs `confirm: true` **and** the SHA-256 of the review the candidate saw. The browser then refills the form, reads it back, and presses submit only if it reproduces exactly that review. The database rejects a `submitted` run without `confirmed_at`, `submitted_at` and `review_hash`. |
| Nothing is sent while preparing | During preparation every non-GET request is aborted by a request gate, so no form can be sent, not even by a page script. A page that tries is treated as unsafe and the run stops. During submission the only request allowed to send data is one request to the form's own action URL, after the click. |
| Never bypass CAPTCHAs, sign-in or access controls | 401/403/407/429 responses, CAPTCHA and bot-challenge pages, sign-in pages and password fields stop the run with an explanation (`app/automation/guard.py`). There is no retry, no stealth, no user-agent spoofing, no stored browser session, and CareerPilot never signs in. |
| Unsupported sites stop with an explanation | Only sites with an adapter are opened. Any other host stops before a browser starts: "isn't a supported application site…". An adapter that doesn't recognise the page (e.g. the layout changed) stops too. A form that sends to a different host is refused. |
| Never invent candidate information | Personal information comes from the profile, documents are the approved resume and cover letter (rendered exactly as downloaded), questions get the approved answer to the same question only. Anything else (e.g. work authorization) is never guessed: the run asks the candidate (`needs_input`). A required question without an approved answer stops for the candidate to add and approve one. |
| Record an audit log | `automation_audit_events` is append-only: who (you / CareerPilot / the browser), what, when, with details (fields filled, file hashes, blocked requests, review hash, confirmation reference). |

## The Application Review screen

Shown only in the `awaiting_review` state, built from the **filled form read back from the
page** (not from what CareerPilot intended to enter):

- **Destination website**: host, application page, and where the form sends.
- **Personal information**: first and last name, email, phone, location, LinkedIn.
- **Resume** and **Cover letter**: file name, approved version, size and SHA-256 of the file
  actually attached.
- **Application answers**: each question as the form asks it, with the approved answer.
- **Additional fields**: values the candidate provided, and optional fields left blank.
- A "Nothing has been submitted yet" banner, a confirmation checkbox, and a Submit button
  that stays disabled until it is ticked. Cancel is always available.

If the form changes between review and submission (a new question, a different option, a
different file), the hash no longer matches and the run stops without submitting.

## Run states

`preparing → needs_input | awaiting_review → submitting → submitted`, or `stopped`
(explained: unsupported site, access control, unsafe page, changed form), `failed` (an
unexpected browser error), or `cancelled`. On success the application moves to
**Submitted** with actor `automation` and the note "after your explicit confirmation", and a
check-in reminder is added, exactly as when the candidate records a submission themselves.

While a run is `submitting`, nothing else may change the application: rejecting or
withdrawing approval, changing its status and starting another run are refused (409), so
the outcome and the approval it relies on are always recorded together. If the browser
fails mid-submission, or the server stops and a `submitting` run is more than 10 minutes
old when the next run starts, the run becomes `failed` with the reason "the submission was
interrupted, so CareerPilot can't tell whether the employer received it". It is never
reported as not submitted. The Application Review screen shows a submitting run as busy and
offers no "Try again".

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/applications/{id}/assisted-runs` | Fill for review (`{inputs}` optional). Never submits. |
| GET | `/api/v1/applications/{id}/assisted-runs` | Runs for an application, newest first |
| GET | `/api/v1/assisted-runs/{run_id}` | A run with its review, problems and audit log |
| POST | `/api/v1/assisted-runs/{run_id}/inputs` | Provide values CareerPilot can't fill, then refill |
| POST | `/api/v1/assisted-runs/{run_id}/submit` | `{review_hash, confirm: true}`: submit exactly the reviewed application |
| POST | `/api/v1/assisted-runs/{run_id}/cancel` | Cancel an open run |

## Code layout

`apps/api/app/automation/`:

- `adapters/`: the `SiteAdapter` protocol (`supports`, `detect`, `submit`) and the registry.
  Only `MockSiteAdapter` exists so far.
- `guard.py`: access-control detection. `plan.py`: what goes in each field, from approved
  materials only. `review.py`: the review model and its canonical hash.
- `runner.py`: the two Playwright phases, each in a fresh headless Chromium that is closed
  afterwards. `service.py`: preconditions, persistence and the audit log.
- `mock_site/`: the mock application website.

## The mock application website

All automated tests run against a local mock site, in a real headless browser, before any
real provider is connected:

```bash
cd apps/api
uv run playwright install chromium        # once
uv run python -m app.automation.mock_site # http://127.0.0.1:8790
```

Set `AUTOMATION_MOCK_SITE_URL=http://127.0.0.1:8790` for the API. The mock adapter is never
enabled when `APP_ENV=production`.

| Page | Exercises |
| --- | --- |
| `/jobs/{slug}/apply` | The supported form: personal fields, resume and cover letter uploads, questions, a required select |
| `/captcha/{slug}/apply` | CAPTCHA → stop |
| `/secure/{slug}/apply` | Redirect to a sign-in page → stop |
| `/blocked/{slug}/apply`, `/ratelimited/{slug}/apply` | 403 and 429 → stop |
| `/changed/{slug}/apply` | An unrecognised layout → stop |
| `/autosubmit/{slug}/apply` | A page script that submits the form on load → blocked, stop |
| `GET/DELETE /__submissions`, `POST /__questions/{slug}` | Inspect what was received; change a form's questions |

The integration tests (`tests/integration/test_assisted_applications_api.py`) prove the site
receives nothing before confirmation, that it then receives exactly the reviewed values and
file hashes, and that every stop condition stops.

## Adding a real provider

Add an adapter that recognises the provider's hosted application form by its structure,
maps its fields to the sections above, and reads its confirmation. Before enabling it:
confirm the provider's terms permit automated form filling, test it against recorded copies
of its forms, and keep every guard in place. Sites that require signing in stay unsupported.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTOMATION_HEADLESS` | `true` | Run Chromium headless |
| `AUTOMATION_TIMEOUT_MS` | `15000` | Per-action browser timeout |
| `AUTOMATION_MOCK_SITE_URL` | unset | Enables the mock-site adapter for that host (not in production) |
