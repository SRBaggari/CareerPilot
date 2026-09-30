# Job Recommendations

Personalized job recommendations, explained from the candidate's verified evidence.

- Code: `apps/api/app/recommendations/`
- API: `/api/v1/recommendations`
- UI: `/recommendations` ("Recommended" in the header)

A recommendation is never just a score. Each one says **why** it is recommended:
- which of the job's requirements the evidence covers;
- the matched and missing skills;
- eligibility concerns;
- the candidate's relevant projects;
- which of the candidate's preferences it meets.

## Pipeline (`POST /api/v1/recommendations/refresh`)

```
Job discovery      every enabled source; one query per preferred role (up to 3), else all
                   postings; newest 30 after the discovery filters
-> Job analysis    the rule-based job analyzer on each posting's text: required and
                   preferred requirements; informational statements dropped
-> Evidence        verified evidence retrieved for each requirement (one embedding call
   retrieval       per posting)
-> Semantic        the existing rule judge and coverage scoring (app.matching.engine),
   matching        unchanged: MATCHED / PARTIAL / MISSING / UNKNOWN per requirement
-> Eligibility     exclusions (filtered out, with the reason) and concerns (shown)
   filtering
-> Recommendation  ranked, explained, and stored per candidate
   list
```

A refresh makes no AI calls: it uses the rule-based analyzer and judge. **Analyze Job**
imports the posting and runs the full analysis, which uses Claude when it's configured.

### Eligibility filtering

| Filtered out (with the reason) | Shown as a concern |
| --- | --- |
| The application deadline has passed | The deadline is within 7 days |
| A job type or work mode outside the candidate's stated preferences | A location outside the preferred locations (for non-remote jobs) |
| Required years of experience at least 3 more than the work history holds | A smaller gap in required years |
| None of the job's required requirements are covered by verified evidence | A required eligibility condition (e.g. work authorization) that can't be verified or isn't met |
| The posting states no requirements, so a recommendation couldn't be explained | A required degree or certification that isn't matched |

Filtered-out jobs aren't hidden: the **Filtered out** view lists each with its reasons.

### Ranking

Eligible jobs are ranked by the facts the explanation shows:
1. share of required requirements covered by evidence;
2. then the number of stated preferences met;
3. then the number of matched skills;
4. then recency.

The card shows "N of M required met", not a percentage in isolation.

## What each recommendation shows

| Field | Source |
| --- | --- |
| Title, company, location, work mode, job type, level, deadline | The normalized posting |
| **Why it's recommended** | A one-sentence summary, plus reasons: how many required requirements the evidence covers and which ones, preferred requirements met, skills the evidence shows, relevant projects, and preferences met |
| **Matched skills** | Technology and skill requirements judged MATCHED, each with the explanation naming the evidence (partly shown skills are listed separately) |
| **Missing skills** | Technology and skill requirements no verified evidence shows |
| **Eligibility concerns** | See above |
| **Relevant projects** | Projects whose verified evidence the matches rest on, with that evidence verbatim and the requirements it covers |
| Every requirement | Each stated requirement with its status and explanation (expandable) |

When the profile changes after a refresh, recommendations are flagged as stale (profile
fingerprint).

## Actions

| Button | API | What happens |
| --- | --- | --- |
| **Save Job** / Unsave | `POST …/{id}/save`, `…/restore` | Saved jobs appear under **Saved** and survive refreshes |
| **Ignore Job** / Restore | `POST …/{id}/ignore`, `…/restore` | Hidden from recommendations and not recomputed on later refreshes, until restored |
| **Analyze Job** | `POST …/{id}/analyze` | Imports the posting as a job, from the stored snapshot, with the same full analysis as a pasted description, then opens its match report |
| **Tailor Resume** | `POST …/{id}/analyze` | Imports the job if needed, then opens its tailored resume page |
| **Start Application** | `POST …/{id}/start-application` | Imports the job if needed and starts tracking it in the [application tracker](application-tracker.md) (status: analyzed), with a status-history entry "Started from a job recommendation". The job is also saved. |

Starting an application doesn't approve or submit anything: approval and recording the
submission remain explicit, separate steps in the tracker. A job with an application can't be deleted.

## Storage

`job_recommendations` holds one row per (candidate, source, posting ID):
- the posting snapshot;
- `status`: new, saved or ignored;
- `eligible`, `exclusions` and `concerns`;
- the full `explanation` (JSONB);
- required and overall coverage, and `rank`;
- the profile fingerprint and `computed_at`;
- `job_id`, once analyzed. It is set to NULL if that job is deleted.

On refresh, postings that disappeared from the sources are dropped, unless the candidate
saved, ignored or analyzed them.

## Tests

- `tests/test_recommendations.py`:
  - requirement analysis of a posting;
  - each eligibility exclusion and concern;
  - the explanation: summary, reasons, matched and missing skills, projects, preference
    fit;
  - preferences that don't match are never claimed.
- `tests/integration/test_recommendations_api.py`:
  - the pipeline over the mock source with a real profile: ranked, explained, relevant
    projects;
  - senior and lead roles filtered out on years, a frontend role on coverage;
  - preferences driving discovery and reasons;
  - an empty profile;
  - save, ignore and restore across refreshes;
  - analyze, then tailor with the unchanged resume pipeline;
  - start application: tracked only, never approved or submitted;
  - stale flags, failing sources, and privacy.
- `apps/web/src/components/recommendations/recommendations.test.tsx`:
  - the card's explanation sections;
  - refresh and source errors;
  - the views and filtered-out reasons;
  - save and ignore;
  - analyze and tailor navigation;
  - starting to track an application.
