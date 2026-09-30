# Job Discovery

Search job sources through provider adapters, then import a posting into CareerPilot. There,
the existing job analysis, matching, resume, cover letter and answer features work on it
unchanged.

- Code: `apps/api/app/discovery/`
- API: `/api/v1/discovery/*`
- UI: `/discover`

**There is no scraping.** Only sources that permit automated access can have adapters.
Nothing bypasses authentication, CAPTCHAs, robots rules or other access controls.

## Architecture

```
            ┌───────────────────── core (source-independent) ─────────────────────┐
 UI/API ──▶ │ registry ─▶ validate_source ─▶ search_jobs (each enabled provider)  │
            │   ─▶ enrich (skills, level) ─▶ filters ─▶ dedupe ─▶ sort ─▶ page    │
            │ import ─▶ get_job ─▶ jobs.analyze_job(provenance) ─▶ Job            │
            └──────────────────────────────┬──────────────────────────────────────┘
                                           │ JobSourceProvider (adapter interface)
                  ┌────────────────────────┼─────────────────────────┐
             MockJobProvider       (future) official API      (future) published feed
```

### `JobSourceProvider` (`provider.py`)

| Member | Purpose |
| --- | --- |
| `name`, `display_name` | Stable identifier (stored with imported jobs) and label |
| `policy: SourcePolicy` | How the source may be accessed (see below) |
| `job_source` | How imported jobs record their origin (`job_board_api`, `ats_api`, `feed`, …) |
| `search_jobs(query)` | Postings for a `JobSearchQuery`, normalized. It may push filters down to the source's API; the core filters the results again either way. |
| `get_job(source_identifier)` | One posting, or `None` |
| `normalize_job(raw)` | The source's raw posting → `NormalizedJob` |
| `validate_source(settings)` | Whether the source may be used, with reasons. It defaults to the shared policy rules. |

### `NormalizedJob` (`models.py`)

| Field | Notes |
| --- | --- |
| `title` | |
| `company` | |
| `location` | |
| `url` | Must be an http(s) URL |
| `source` | The provider's name |
| `description` | |
| `employment_type` | `full_time`, `part_time`, `internship`, `contract`, … |
| `work_mode` | `onsite`, `hybrid` or `remote` |
| `posted_date` | |
| `deadline` | |
| `source_identifier` | The provider's own ID |
| `skills`, `experience_level` | For filtering. The provider may supply them; otherwise they come from the posting's explicit wording. A title saying "Senior" gives `senior`, and an internship gives `student`; nothing else is guessed. |

### Filters (`filters.py`, applied identically to every source)

| Filter | Behaviour |
| --- | --- |
| Role | Every word appears in the title. Common abbreviations are understood: "ML engineer" finds "Machine Learning Engineer". |
| Location | Case-insensitive substring of the location |
| Remote | `true` = remote only; `false` = not remote; unset = either |
| Job type | Internship, full-time, part-time, contract (any of those selected) |
| Skills | Postings naming **any** requested skill. Postings naming the most requested skills come first, and the matched skills are reported. |
| Experience level | Any of the selected levels. A posting whose level isn't stated is excluded when this filter is set. |

Results are de-duplicated by URL across sources and sorted by skills matched, then newest.
They are paginated with up to 50 per page.

## Access rules (`access.py`)

Every provider declares a `SourcePolicy`. `validate_policy` enforces:

- **Kinds:** only `official_api`, `public_feed` or `mock`. There is no scraper kind.
- **Permission:** the source's terms must permit automated access, and the terms URL is
  recorded.
- **Feeds:** must honour robots.txt (`robots_allows`).
- **Credentials:** API credentials must be configured through a setting, never
  hard-coded. A missing key disables the source with a message saying which setting to
  set.
- **Rate limits:** requests must be at least 0.5 s apart.
- **Mock:** the mock provider is disabled in production.

`check_response` turns refusals into `AccessDenied`. These are:
- 401 or 407: authentication required;
- 403: forbidden;
- 429: rate limited (Retry-After is reported);
- a CAPTCHA or bot-challenge page.

The request is abandoned with no retry, and the source's error is shown next to the other
sources' results. A failing source never breaks the others.

Sources are enabled by name with `DISCOVERY_PROVIDERS` (default `mock`). Names without an
adapter, and sources that fail validation, are listed as unavailable with the reason
(`GET /api/v1/discovery/sources`).

## Import

`POST /api/v1/discovery/jobs/{source}/{source_identifier}/import`:

- The posting is fetched with `get_job`.
- The description goes through **the same job analysis as a pasted job**
  (`jobs.analyze_job`). Requirements are classified as required, preferred or
  informational, and nothing is inferred.
- The source's structured fields (work mode, employment type, posting date, deadline) take
  precedence over values extracted from the text.
- The job records `source`, `source_name` (the provider), `external_id` (the posting's
  ID), `url` and `input_method = discovered`.
- It is imported once per user: importing again returns the existing job (200 rather
  than 201).
- Two users can import the same posting independently, since the unique key is
  (user, source, provider, posting ID); migration `0012` changed it from a global key.

Nothing else is specific to discovery. Matching, resumes, cover letters and answers treat
an imported job like any other.

## The mock provider

`providers/mock.py` serves 20 fixed sample postings (`mock_jobs.json`) with no network
access. They cover:
- internships, full-time, part-time and contract roles;
- remote, hybrid and on-site work;
- levels from student to lead;
- ML, data, backend, frontend, DevOps and other roles.

Its raw format differs from `NormalizedJob`, so `normalize_job` does real mapping work.

## Adding a real provider

1. Confirm the source offers an official API or published feed whose terms permit this
   use.
2. Subclass `JobSourceProvider` with a `SourcePolicy` (kind, terms URL,
   `automated_access_permitted=True`, `credential_setting` if a key is needed, a request
   interval).
3. Call the API with the `USER_AGENT`. Pass every response through `check_response`, and
   for feeds, check `robots_allows` first.
4. Implement `normalize_job`, and register a factory in `registry.PROVIDERS`.
5. The provider-contract test in `tests/test_discovery.py` runs against every registered
   adapter automatically.

## Tests

- `tests/test_discovery.py`:
  - the provider contract, run for every adapter;
  - mock normalization, and malformed input;
  - URL safety and level inference;
  - each filter, and ranking;
  - the policy rules: permission, terms, robots, credentials, rate limits, mock only in
    development;
  - `check_response` for 401, 403, 407, 429 and CAPTCHA challenges;
  - robots.txt handling;
  - the registry: unknown and invalid sources are never enabled.
- `tests/integration/test_discovery_api.py`:
  - sources, search, pagination, each filter and combinations, skill ranking,
    validation, and detail;
  - import with provenance, extracted requirements, re-import, the imported flag, and
    **matching the imported job with the unchanged engine**;
  - per-user imports;
  - a failing source, a CAPTCHA-guarded source (not retried) and an unpermitted source,
    none of which affect the others.
- `apps/web/src/components/discovery/discovery.test.tsx`: query encoding, results, every
  filter, import, source errors, pagination, and no results.
