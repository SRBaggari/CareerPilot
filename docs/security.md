# Security: threat model and mitigations

CareerPilot holds a candidate's career history, writes documents about them with an AI
model, and can fill and submit job applications in a browser. This document describes what
it protects, who it protects against, and how. It reflects the security audit of
2026-10-01; the "Fixed in the audit" notes mark changes made then.

## Assets

- **Candidate data**: profile, evidence, uploaded resumes, generated documents, answers,
  applications. Private to the candidate.
- **Candidate facts**: what CareerPilot may say about the candidate. They change only by
  the candidate's own action.
- **Approval and submission**: nothing is submitted without the candidate's explicit
  approval of the exact content.
- **Secrets**: AI provider keys, the database password.
- **The host**: the server, its network, and its file system.

## Adversaries

| Adversary | Can control |
| --- | --- |
| A job posting author | Job descriptions (pasted or discovered), application page content |
| A hostile application site | Everything a browser loads from it, including scripts |
| A malicious document | Uploaded PDF/DOCX content (crafted structure, hidden text) |
| Another website the candidate visits | Requests the candidate's browser sends to the local API |
| Another user (once multi-user auth exists) | Their own account, and any ID they can guess |
| A compromised or manipulated AI model | Anything the model returns |

## Prompt injection

**A job description is untrusted data.** So is everything derived from it, along with
uploaded resumes, application questions, and pages loaded by browser assistance. Text in
them can try to make the AI model reveal secrets, change its instructions, access files,
run commands, alter the candidate's facts, or bypass approval. CareerPilot's defense is
mostly structural: even a model that obeys every injected instruction cannot cause harm.

| The attacker wants to | Why it can't happen |
| --- | --- |
| Reveal secrets | The model never receives a secret: no API keys, environment, file contents or other users' data are put in prompts. There is nothing to reveal. Every log is redacted as well (see Logging). |
| Change system instructions | Untrusted text is fenced: `<` and `>` become look-alike characters (`fence`) or JSON escapes (`safe_json`), so the data can't close its tag and pose as instructions. Every system prompt ends with rules stating that tagged data is never an instruction (`with_rules`); the provider adds them to any prompt that lacks them. *Fixed in the audit: tags could previously be closed by the data.* |
| Access unrelated files or run commands | The model has **no tools**. It can only return JSON in a fixed schema. Nothing in CareerPilot executes model output, and there is no `subprocess`, `eval`, shell or dynamic file access anywhere in the app. |
| Modify candidate facts | The model can't write the profile. Job analysis output is **grounded**: any value not found verbatim in the posting is discarded. Resume parsing creates *suggestions* the candidate must accept. Every generated claim goes through the **claim verification engine** against the candidate's own evidence. Rule checks decide, and an AI reviewer may only make a verdict stricter, or upgrade one when its cited evidence also passes the rule checks. |
| Bypass approval | Approval and submission are human actions on separate endpoints, bound to a content hash. The agent's state machine is code, not model-driven: no tool approves or submits, and the agent stops at APPROVE and SUBMIT for the human. |
| Steer the agent | The agent doesn't ask the model what to do next. Its tools and transitions are fixed and validated by the state machine (see [agent.md](agent.md)). |

Detection is a complement, not a defense. Instruction-like text in a posting or resume
("ignore previous instructions", "reveal the API key", "mark as verified", "approve and
submit automatically", "say the candidate has…", shell commands, `file://`) is flagged.
The candidate sees: "This job description contains text that looks like instructions to
an AI (…). CareerPilot treated it as plain data and did not follow it." (`app/ai/untrusted.py`).

**Tested** (`tests/integration/test_prompt_injection.py`): a malicious posting goes through
every AI path with an *adversarial model* that fills every field with fabricated facts,
"mark this verified", "system prompt", and "approve and submit". The tests check that:

- the posting can't escape its fence;
- the rules are in every prompt;
- no prompt contains the configured key;
- fabricated claims are never verified into a document;
- the profile is unchanged;
- the agent never gets past approval;
- nothing is approved or submitted;
- the key appears in no stored table.

## Authentication and authorization

- **Development**: `AUTH_MODE=dev`; `DEV_USER_EMAIL` is the identity, given only to
  requests from the local machine (loopback).
- **Production**: `AUTH_MODE=proxy` (required; the API refuses to start otherwise). An
  authenticating reverse proxy signs the user in and forwards their email and a shared
  secret (32+ characters, compared in constant time). Requests without the secret get 401,
  and the proxy overwrites both headers, so clients can't forge them. `/docs` is off. See
  [deployment.md](deployment.md).
- **Ownership**: every personal record is reached through the caller's profile (or
  `Job.created_by_user_id`); another user's IDs return 404. Body IDs (attached documents,
  interviews, coursework, evidence subjects, the agent's job) are checked the same way.
- *Fixed in the audit (high)*: a tailored-resume edit could cite another candidate's
  evidence ID, and the API returned that evidence's text. Client-supplied citations are
  now reduced to the candidate's own evidence before verification. Every evidence lookup
  (resume, cover letter, answers, match report) is scoped to the owner as defense in depth.
- Mass assignment: update schemas are allow-lists (`extra="forbid"` where it matters);
  status, approval state, timestamps and owner can't be set by clients.

## API and HTTP

| Threat | Mitigation |
| --- | --- |
| SQL injection | All queries use SQLAlchemy with bound parameters; sorting is applied in Python after a `Literal` check. No string-built SQL. Search terms are stripped of `%`, `_` and `\` (*fixed: a trailing backslash caused a 500*). |
| CSRF / cross-site requests | The API uses no cookies today. *Fixed*: `CrossSiteGuard` refuses state-changing requests whose `Origin` isn't an allowed origin, or that are marked `Sec-Fetch-Site: cross-site`. Browsers send these on every cross-site POST, including no-preflight multipart uploads. CORS allows only listed exact origins (a `*` is rejected at startup), listed methods and the `Content-Type`/`Authorization` headers. |
| DNS rebinding (a site re-pointing its name at 127.0.0.1) | *Fixed*: `TrustedHostMiddleware` answers only to `ALLOWED_HOSTS` (default `localhost`, `127.0.0.1`, `[::1]`). |
| Oversized requests | *Fixed*: `BodySizeLimit` refuses bodies over `MAX_REQUEST_BYTES` (10 MB) before parsing, streamed or not. Edit payloads have per-field limits (resume claims ≤2000 characters, ≤50 entries per section, ≤20 citations; cover letter paragraphs ≤3000 characters; agent questions ≤1000 characters). |
| Clickjacking, sniffing, caching | *Fixed*: API responses carry `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `no-referrer`, and `Cache-Control: no-store`. The web app sends a CSP (scripts and connections only to itself and the API, no framing, no plugins), plus `X-Frame-Options`, `nosniff`, `Referrer-Policy` and `Permissions-Policy`. |
| Error leakage | Domain errors carry user-safe messages; validation errors omit the input; 500s are generic. *Fixed*: the agent stores only the error type for unexpected errors (details go to the server log). Discovery provider errors are no longer echoed. |

## XSS

React escapes all text. The one `dangerouslySetInnerHTML` is a constant (the theme
script). Every link built from data (job URLs, application URLs, profile links) points to
a field the backend restricts to http(s) URLs with a host on every write path
(`normalize_url`, `_http`, `NormalizedJob`), so `javascript:`, `data:` and `file:` links
can't be stored. *Fixed*: `_http` now requires a host, not just a prefix. All
`target="_blank"` links use `rel="noopener noreferrer"`. The CSP is a second layer.

## File uploads and documents

- The format comes from magic bytes, and must match the extension. *Fixed*: the PDF
  signature must start the file, so HTML/PDF polyglots are refused. Legacy `.doc`/OLE is
  refused.
- Limits: 5 MB upload, 20 PDF pages, 100,000 characters of text. DOCX gets an entry-count
  limit and *(fixed)* a 10 MB uncompressed limit against zip bombs. XML entities are
  disabled in python-docx's parser (no XXE). Encrypted PDFs are refused.
- *Fixed*: parsing runs off the event loop with a 20-second timeout, and every parser
  error becomes a safe "could not be read" message, never a 500.
- Storage keys are generated by the server (`resumes/{profile}/{uuid}.{ext}`), and paths
  are checked to stay inside the storage root. The client's filename is metadata only, so
  there is no path traversal. Original uploads are never served back.
- Generated PDFs and DOCX use plain-text rendering (no HTML or markdown mode). Download
  filenames are reduced to `[A-Za-z0-9-]`, so headers can't be injected.
- Uploaded resume text is untrusted for prompt injection too (see above).

## SSRF

The server makes outbound requests only to the configured AI and embedding providers (fixed
endpoints). Job `source_url`s are stored, never fetched; there is no "import from URL".
Discovery uses provider adapters with access rules, and the mock provider is refused in
production. Browser assistance is covered below.

## Browser automation and application submission

| Threat | Mitigation |
| --- | --- |
| Opening an arbitrary or internal URL | Only hosts with an adapter are opened (the mock site only, never in production); `file:`, `data:` and other schemes are refused before any browser starts. *Fixed*: adapters compare scheme, host and port exactly. A redirect to another origin stops the run. |
| A page sending the candidate's data elsewhere | *Fixed*: the request gate covers the **whole browser context** (popups too). It allows only same-origin GETs while filling, and never contacts any other site, so beacons, fetches and trackers to other origins are dropped. Service workers are blocked, WebSockets are closed, and popups are closed and treated as unsafe. |
| A page submitting by itself | Every non-GET request is blocked while preparing. One that is attempted makes the run stop as unsafe. |
| Submission without consent | Submission needs the application approved and unchanged (the approval gate), plus `confirm: true` and the review hash the candidate saw. The browser refills the form and submits only if it reads back to that exact review. *Fixed*: a missing file hash counts as a mismatch, and the single allowed submission must be the main frame's navigation to the form's own action, not a script's request. |
| Selector or fill tricks | *Fixed*: element IDs supplied by the page are escaped in selectors. |
| Sign-ins, CAPTCHAs, rate limits | Detected, and the run stops with an explanation; never bypassed. |
| Downloads and file access | Downloads are disabled; files are attached from in-memory buffers, never paths. Each phase uses a fresh browser that is then closed. |
| Crashes leaving state behind | *Fixed*: any unexpected error fails the run safely and says whether submit had been pressed. |

## AI tool permissions

- **Language model**: no tools, no browsing, no file access; JSON-schema output only.
  Output is validated by code (schema, grounding, verification) before it has any effect.
- **Agent**: 12 explicit tools that call existing services under the state machine. They
  can't approve or submit; human input is limited to an allow-listed decision
  (`confirm_eligibility`). Runs are bounded (at most one pass per request, 60 tool calls
  per run), and a run can't use another user's job.

## Secrets and logging

- Secrets are `SecretStr` settings read at the point of use; nothing returns settings; no
  secrets are in the repository (`.env*` is git-ignored; templates are kept).
- The agent's execution log, its pause messages and errors pass through `redact`: the
  configured secret values, the database password, and key-, token- and password-shaped
  strings are removed (`app/agent/redact.py`).
- *Fixed*: automation audit URLs are stored without query strings. Third-party loggers
  (`anthropic`, `httpx`, `sqlalchemy.engine`) are capped at WARNING, so prompts and SQL
  parameters aren't logged. `DATABASE_ECHO` is refused in production.
- *Fixed*: the local Postgres container listens on 127.0.0.1 only.

## Known limitations and next steps

- **First-party login** is not implemented; production relies on the authenticating
  proxy. If a first-party login is added, prefer a bearer token. With cookies, keep the
  Origin check, use `SameSite=Strict`, and never allow credentialed wildcard CORS.
- **Rate limits and pagination**: AI-backed endpoints and list endpoints have no per-user
  rate limit or pagination yet. In a multi-user deployment, add them at the proxy or app
  level.
- **Shared skill vocabulary**: skill names and categories are shared across candidates.
  The first spelling wins. This is harmless with one user; in multi-user deployments keep
  categories per candidate.
- **Real application sites**: before enabling a real adapter, review the provider's
  terms. Add allow-listed asset origins to the gate if the site needs them, and consider
  comparing the submitted body with the review (unreviewed hidden inputs on a hostile but
  supported site are a residual risk).
- `/health` reports the environment name. Restrict it at the proxy if that matters.

## Security tests

| Area | Tests |
| --- | --- |
| Prompt injection | `tests/integration/test_prompt_injection.py` (adversarial model, end to end); `tests/test_security.py` (fencing, JSON escaping, rules, detection without false positives on ordinary postings) |
| HTTP layer | `tests/test_security.py`: headers, trusted hosts, cross-site refusal, body limits, CORS validation |
| Authorization | `tests/integration/test_security_api.py` (cross-tenant evidence), `test_approval_api.py`, `test_agent_api.py`, `test_applications_api.py` (cross-user 404s), `test_current_user.py` (loopback-only dev login, production refusal) |
| Input handling | `test_security_api.py` (oversized edits, search patterns, unsafe links), `test_security.py` (polyglot files) |
| Browser automation | `test_assisted_applications_api.py`: CAPTCHA, login, 403/429, unknown layouts, self-submitting pages, **popups, redirects, and exfiltration to other origins**, form changes, confirmation and approval gates |
| Secrets | `test_agent.py` and `test_agent_api.py` (redaction), `test_prompt_injection.py` (no key in prompts or stored tables) |
