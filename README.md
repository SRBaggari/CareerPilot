# CareerPilot

CareerPilot is a personal AI job application agent for students and job seekers. It takes a
candidate's own evidence (resume, projects, experience) and helps them find, tailor, and prepare
applications, while a human approves every step that matters.

```
Master profile → Job discovery → JD analysis → Semantic matching → Skill-gap analysis
  → Tailored resume → Tailored cover letter → Claim verification
  → HUMAN APPROVAL → Browser-assisted application (stops before submit) → Tracking
```

> **Status: Job matching.** Master profile (`/profile`), resume upload with reviewed
> extraction, verified-evidence search, job description analysis (`/jobs`), and explainable
> candidate-job matching (`/jobs/[id]/match`). No job discovery or document generation yet.
> See [docs/matching.md](docs/matching.md), [docs/job-analysis.md](docs/job-analysis.md),
> [docs/evidence-retrieval.md](docs/evidence-retrieval.md),
> [docs/resume-ingestion.md](docs/resume-ingestion.md), [docs/profile.md](docs/profile.md),
> and [docs/database.md](docs/database.md).

## Non-negotiable principles

1. **No invented candidate information.** Everything comes from stored evidence.
2. **Traceable claims.** Every AI-generated claim must link to the evidence that supports it.
3. **Human approval before any submission.** Browser automation stops before the final submit.
4. **Respectful automation.** No scraping sites that prohibit it; no bypassing auth or CAPTCHA.
5. **Secrets live in environment variables**, never in code.

## Repository layout

```
apps/
  web/     Next.js (App Router) + TypeScript + Tailwind frontend
  api/     FastAPI + SQLAlchemy (async) + Alembic backend
infra/     docker-compose for PostgreSQL + pgvector
docs/      Architecture and design docs
```

See [docs/architecture.md](docs/architecture.md) for the full design.

## Prerequisites

| Tool       | Version  | Notes                                                         |
| ---------- | -------- | ------------------------------------------------------------- |
| Node.js    | ≥ 20.9   | npm workspaces                                                |
| uv         | ≥ 0.5    | Python package/venv manager; installs Python 3.12 if missing  |
| PostgreSQL | 16+      | With the `pgvector` extension. Easiest: Docker (see below)    |

## Getting started

```bash
# 1. Install dependencies
npm install                    # JS deps (root + apps/web)
cd apps/api && uv sync && cd ../..

# 2. Configure environment
cp apps/web/.env.example apps/web/.env.local
cp apps/api/.env.example apps/api/.env

# 3. Start PostgreSQL + pgvector and run migrations
npm run db:up                  # requires Docker
npm run db:migrate

# 4. Run the apps (in two terminals)
npm run dev:api                # http://localhost:8000  (docs at /docs)
npm run dev:web                # http://localhost:3000
```

Open http://localhost:3000/profile to build your master profile. Until authentication is
added, the API acts as the local user set by `DEV_USER_EMAIL` in `apps/api/.env`.

Resume parsing works offline with a rule-based parser. To use Claude for extraction, set
`ANTHROPIC_API_KEY` in `apps/api/.env` (`RESUME_PARSER=auto` picks it up). Evidence search
uses offline lexical embeddings by default; set `EMBEDDING_PROVIDER=voyage` and
`VOYAGE_API_KEY` for semantic search.

Check the backend: `GET http://localhost:8000/health` (liveness) and
`GET http://localhost:8000/health/ready` (database + pgvector readiness).

## Common commands

All commands are run from the repository root.

| Command                | What it does                                            |
| ---------------------- | ------------------------------------------------------- |
| `npm run dev:web`      | Next.js dev server                                      |
| `npm run dev:api`      | FastAPI dev server with auto-reload                     |
| `npm test`             | All tests (Vitest + pytest)                             |
| `npm run typecheck`    | `tsc` (after `next typegen`) + `mypy --strict`          |
| `npm run lint`         | ESLint + Ruff lint + Ruff format check                  |
| `npm run format`       | Prettier + Ruff format (writes changes)                 |
| `npm run build:web`    | Production build of the frontend                        |
| `npm run db:up/down`   | Start/stop local Postgres (Docker)                      |
| `npm run db:migrate`   | Apply Alembic migrations                                |

Per-app equivalents: `npm run <script> --workspace web`, or `uv run <tool>` inside `apps/api`.

### Integration tests

Integration tests (`tests/integration/`) need a **disposable** PostgreSQL + pgvector database
and are skipped unless `TEST_DATABASE_URL` is set. The suite drops and re-creates the schema,
so never point it at your development database:

```bash
cd apps/api
TEST_DATABASE_URL=postgresql+asyncpg://careerpilot:careerpilot@localhost:5432/careerpilot_test uv run pytest
```
