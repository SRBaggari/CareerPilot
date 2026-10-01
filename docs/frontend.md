# The CareerPilot web app

A Next.js App Router frontend (`apps/web`) with one app shell, a dashboard, and a page per
section. It talks only to the CareerPilot API; the dashboard and section pages are built
from existing endpoints (no backend changes were needed).

## Sections

| Section | Route | What it's for |
| --- | --- | --- |
| Dashboard | `/` | Jobs discovered, saved jobs, applications prepared and submitted, interviews, follow-ups; what needs your attention; upcoming interviews; recent activity |
| Profile | `/profile` | Your master profile, resume upload, evidence |
| Jobs | `/jobs` | Every job you analyzed or imported (`/jobs/[id]`: its analysis, match, documents) |
| Job Analysis | `/analyze` | Paste or enter a job description and analyze it |
| Recommended Jobs | `/recommendations` | Explained recommendations from discovery (and `/discover` to search sources) |
| Applications | `/applications` | The tracker: board, list, filters, reminders (`/applications/[id]`: detail) |
| Resume Builder | `/resumes` | Your uploaded resume, and the tailored resume for each job (open, create, download) |
| Cover Letters | `/cover-letters` | The cover letter for each job |
| Application Review | `/review` | Applications ready for your review, approved, being prepared, rejected (`/applications/[id]/review`: the review itself) |
| Settings | `/settings` | Theme, account, API and database status, job sources, safety guarantees, how CareerPilot works |

Also in the navigation: the application **Agent** (`/agent`) and **Check claims** (`/verify`).

### Dashboard numbers

| Tile | Source |
| --- | --- |
| Jobs discovered | jobs you analyzed or imported |
| Saved jobs | applications in *Saved* |
| Applications prepared | *Application prepared* + *Awaiting approval* |
| Applications submitted | *Submitted* and every later stage (assessment, interview, offer) |
| Interviews | applications at *Interview*, plus how many are scheduled in the next two weeks |
| Follow-ups | reminders overdue or due this week |

Recent activity merges newly added jobs, recently updated applications and agent runs,
newest first. If one source fails to load, the rest still shows, with a notice. A new user
without a profile sees a short "Get started" guide instead.

## Layout and responsiveness

- `components/shell/AppShell.tsx` wraps every page (in the root layout): a fixed sidebar on
  large screens; on small screens a top bar with a menu button that opens a navigation
  drawer (focus moves into it; Escape or choosing a page closes it). A skip link jumps to
  the content. The current section is highlighted, including on nested pages (e.g. a job's
  tailored resume highlights *Resume Builder*).
- Pages use a 16px side gutter on phones, wrap their action rows, stack label/value pairs,
  and scroll wide boards and tables inside their own container, so no page scrolls
  sideways at phone width (checked at 390px and 1280px).
- Dark mode follows the device, or the choice in Settings (stored in this browser only, and
  applied before the page paints).

## Tests

Vitest and Testing Library, run with `npm test --workspace web`. Flows covered include:
navigating every section and the mobile menu; the dashboard numbers, attention list,
activity feed, first-run guide and partial failures; the Resume Builder and Cover Letters
lists (open, create, download, one failing job); the Application Review groups; the theme
setting and service status; analyzing a job; plus the existing profile, jobs, matching,
resume, cover letter, answers, applications, review, browser-assistance and agent flows.
