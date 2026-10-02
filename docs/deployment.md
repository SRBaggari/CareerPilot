# Deploying CareerPilot

This guide covers a production deployment on one Linux server with Docker, the
configuration behind it, how to operate it, and how to run the whole system locally from
a fresh clone. Nothing deploys automatically: every step here is run by you.

## Architecture

```
            internet (HTTPS 443, HTTP 80 → redirect)
                          │
                 ┌────────▼────────┐  Let's Encrypt certificates, login (basic auth),
                 │      Caddy      │  HSTS, compression, routing
                 └───┬─────────┬───┘
       /api/*, /health│         │ everything else
            ┌─────────▼───┐ ┌───▼──────────┐
            │  API        │ │  Web         │
            │  FastAPI    │ │  Next.js     │
            │  + Chromium │ │  standalone  │
            └──┬───────┬──┘ └──────────────┘
               │       │
     ┌─────────▼──┐ ┌──▼──────────────┐
     │ PostgreSQL │ │ file storage    │
     │ + pgvector │ │ (volume)        │
     └────────────┘ └─────────────────┘
```

Files: `deploy/docker-compose.yml`, `deploy/Caddyfile`, `apps/api/Dockerfile`,
`apps/web/Dockerfile`, `.env.example`.

- **One origin.** The web app and the API share `https://DOMAIN`; Caddy routes `/api/*` and
  `/health*` to the API. The browser makes no cross-origin calls, so CORS is only a safety
  net, and the login applies to the API too.
- **Only Caddy is exposed.** PostgreSQL and the API listen on the internal Docker network
  only.

## Production deployment (exact steps)

### 1. Prepare the server

- A Linux VM with at least 2 vCPUs and 4 GB RAM (Chromium for browser-assisted
  applications needs about 1 GB), and 20 GB disk.
- Docker Engine 24+ with the Compose plugin (`docker compose version`).
- A DNS `A` (and `AAAA`, if you use IPv6) record for your domain pointing at the server.
- Inbound ports 80 and 443 open (TCP, plus UDP 443 for HTTP/3). Port 80 is needed for the
  Let's Encrypt challenge and redirects to HTTPS.

### 2. Get the code and configure it

```bash
git clone https://github.com/SRBaggari/CareerPilot.git
cd CareerPilot
cp .env.example .env
chmod 600 .env
```

Edit `.env`. Every variable is documented in `.env.example`. The required ones:

```bash
# Secrets: generate fresh values, never reuse the examples
openssl rand -hex 32        # → AUTH_PROXY_SECRET
openssl rand -hex 24        # → POSTGRES_PASSWORD (letters and digits only)

# Your login password, hashed for Caddy (keep the single quotes around it in .env)
docker run --rm caddy:2 caddy hash-password --plaintext 'a-long-unique-password'
```

| Variable | Value |
| --- | --- |
| `DOMAIN` | e.g. `careerpilot.example.com` |
| `ACME_EMAIL` | your email, for Let's Encrypt |
| `BASIC_AUTH_USER` | your email address (also your CareerPilot account) |
| `BASIC_AUTH_PASSWORD_HASH` | output of `caddy hash-password`, in single quotes |
| `AUTH_PROXY_SECRET` | output of `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | output of `openssl rand -hex 24` |
| `ANTHROPIC_API_KEY` | optional: enables Claude for parsing, analysis and writing |
| `EMBEDDING_PROVIDER`, `VOYAGE_API_KEY` | optional: `voyage` for semantic search |

### 3. Build the images

```bash
docker compose --env-file .env -f deploy/docker-compose.yml build
```

The web image compiles `NEXT_PUBLIC_API_BASE_URL=https://DOMAIN` into the browser bundle.
If `DOMAIN` changes, rebuild it. To leave out Chromium (and browser-assisted
applications), add `--build-arg INSTALL_BROWSER=false` to the API build.

### 4. Start

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d
```

Start order: `db` → `migrate` (runs `alembic upgrade head`, then exits) → `api` → `web`
→ `caddy`. The API refuses to start with an unsafe production configuration and says
why: for example a short `AUTH_PROXY_SECRET`, non-https origins, or development settings
left on.

### 5. Verify

```bash
docker compose --env-file .env -f deploy/docker-compose.yml ps        # all running; migrate exited 0
docker compose --env-file .env -f deploy/docker-compose.yml exec api python -m app.check --production
curl -fsS https://$DOMAIN/health                                       # {"status":"ok",...}
curl -fsS -u 'you@example.com:your-password' https://$DOMAIN/health/ready
```

`app.check` must end with "All required checks passed." Then open `https://DOMAIN`, sign
in with `BASIC_AUTH_USER` and your password, and create your profile.

### 6. Update to a new version

```bash
git pull
docker compose --env-file .env -f deploy/docker-compose.yml build
docker compose --env-file .env -f deploy/docker-compose.yml up -d     # migrations run first
```

Back up before updating (step 7). Migrations only move forward in production; to roll
back, restore the backup taken before the update and redeploy the previous version.

### 7. Back up and restore

```bash
# Database
docker compose --env-file .env -f deploy/docker-compose.yml exec -T db \
  pg_dump -U careerpilot -d careerpilot -Fc > careerpilot-$(date +%F).dump
# Uploaded resumes
docker run --rm -v careerpilot_storage:/data -v "$PWD":/backup alpine \
  tar czf /backup/storage-$(date +%F).tgz -C /data .
```

Restore into a stopped stack: `pg_restore --clean --if-exists -d careerpilot` (inside the
`db` container), and extract the storage archive into the `careerpilot_storage` volume.
Keep backups encrypted: they contain personal data.

## Configuration reference

### Environment variables

Every variable is listed and explained in [`.env.example`](../.env.example) (production)
and [`apps/api/.env.example`](../apps/api/.env.example) (development); a test keeps both
complete. Neither contains real keys. Secrets are only ever read from the environment.

### Frontend (Next.js)

- Built as a standalone Node.js server (`NEXT_OUTPUT=standalone`, set by the Dockerfile),
  run as the unprivileged `node` user on port 3000.
- `NEXT_PUBLIC_API_BASE_URL` is a build argument; it is public (in the browser bundle) and
  must never contain secrets.
- Security headers (CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`,
  `Permissions-Policy`) come from `next.config.ts`; Caddy adds HSTS.

### Backend (FastAPI)

- Uvicorn with `WEB_CONCURRENCY` worker processes (default 2; about one per CPU core),
  `--proxy-headers` (the client IP comes from Caddy), and graceful shutdown.
- Runs as an unprivileged user (uid 10001). Interactive API docs are off in production.
- Production configuration is validated at startup (`production_problems` in
  `app/core/config.py`): proxy authentication with a 32+ character secret, https origins,
  real host names, no development login, mock site, sample job source or SQL echo.

### Authentication

Production uses `AUTH_MODE=proxy`. Caddy asks for your login (HTTP basic auth over HTTPS).
It then forwards your email in `X-CareerPilot-User` and `AUTH_PROXY_SECRET` in
`X-CareerPilot-Proxy-Secret`, overwriting anything a client sends. The API accepts the
user only with the right secret (compared in constant time). A request that reaches the
API another way can't claim an identity. To add users, add lines to `basic_auth` in the
Caddyfile.

**Single sign-on:** replace `basic_auth` with oauth2-proxy (Google, GitHub, Okta…) via
Caddy's `forward_auth`, and forward the authenticated email in the same header.

### CORS

The web app and API share one origin, so browsers make no CORS requests. The compose file
sets `CORS_ORIGINS=https://DOMAIN` (exact origins only; a `*` is refused at startup). The
same list drives the cross-site request guard, which refuses state-changing requests sent
from other sites. If you serve the API from a different host, add the web app's exact
origin.

### HTTPS

Caddy obtains and renews certificates from Let's Encrypt automatically. It redirects HTTP
to HTTPS and sends `Strict-Transport-Security`. Certificates live in the `caddy_data`
volume (keep it; Let's Encrypt rate-limits re-issuance). Behind a load balancer or CDN
that terminates TLS, change the Caddyfile's site address to `:80`, and make sure the
balancer forwards `Host` and `X-Forwarded-*` and redirects HTTP to HTTPS.

### PostgreSQL

- Image `pgvector/pgvector:pg17`; data in the `pgdata` volume; no published port.
- Connection: `DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB` (the asyncpg
  driver is required). For a managed database requiring TLS, append `?ssl=require`.
- Sizing: the default pool (5 connections + 10 overflow per worker) suits the default 2
  workers; keep `max_connections` above `WEB_CONCURRENCY × 15`.
- **Managed PostgreSQL** (RDS, Cloud SQL, Azure, Neon, Supabase…): use version 16+ with
  pgvector 0.5+, set `DATABASE_URL`, remove the `db` service, and drop the `db` dependency
  from `migrate`.

### pgvector

- Migration `0001` runs `CREATE EXTENSION IF NOT EXISTS vector`. On managed services
  where the migration user can't create extensions, run it once as an admin:
  `CREATE EXTENSION vector;`.
- Embeddings are 1024-dimensional (`EMBEDDING_DIMENSIONS` in `app/db/vector.py`), indexed
  with HNSW for cosine distance. Changing the embedding model's dimension requires a
  migration and re-embedding.
- `/health/ready` and `app.check` report whether the extension is installed.

### Database migrations

- Alembic, in `apps/api/migrations`. The compose `migrate` service runs
  `alembic upgrade head` before the API starts, on every deploy (no-op when current).
- Run by hand: `docker compose ... run --rm migrate`.
- `/health/ready` returns 503 if the schema isn't at the latest migration shipped with the
  code, so a load balancer won't send traffic to an API whose schema is behind.

### Production logging

- `LOG_FORMAT=json`: one JSON object per line on stdout, for any log collector
  (`docker compose logs -f api`, Loki, CloudWatch…). Fields: `time`, `level`, `logger`,
  `message`, `request_id`, plus `method`, `path`, `status`, `duration_ms` on access lines.
- Every request carries an `X-Request-ID` (from Caddy, or generated). It's returned in the
  response and attached to every log line written while handling that request.
- Never logged: request or response bodies, query strings, headers, or secrets. AI and SQL
  libraries are capped at WARNING so prompts and SQL parameters don't reach logs, and
  `DATABASE_ECHO` is refused in production. The agent's execution log in the database is
  redacted separately (see [security.md](security.md)).
- Caddy writes JSON access logs too. Rotate container logs with Docker's `json-file`
  `max-size`/`max-file` options, or ship them to a collector.

### Error handling

- Expected problems return a clear message and the right status (404, 409, 422…).
- An unexpected error returns `500` with a generic message and a reference: "Something went
  wrong on our side… quote reference `<request id>`". The details, which may include SQL
  or personal data, go only to the log, under that request ID.
- Configuration errors at startup list what to fix, and never echo the values (which may
  be secrets).

### Health checks

| Endpoint | Meaning | Used by |
| --- | --- | --- |
| `GET /health` (also `HEAD`) | The process is up (no database access). Public, so uptime monitors work without a login | Docker `HEALTHCHECK`, uptime monitors |
| `GET /health/ready` (also `HEAD`) | Database reachable, pgvector installed, schema at the latest migration, file storage writable; otherwise `503` with details | load balancers, deploy checks |
| `python -m app.check --production` | All of the above, plus configuration, browser and AI-key checks, with exit status | deploy scripts, after updates |

The web image has its own `HEALTHCHECK` on `/`.

### Background jobs

**None are needed.** All work happens within requests:

- Generation and verification run while you wait (seconds).
- Browser-assisted applications run a headless browser inside the request (up to about a
  minute). Caddy's API timeout is 180 seconds for this.
- Follow-up reminders are computed when the dashboard is read; nothing is sent, ever.
- Job recommendations refresh when you ask.

There is no queue, worker or cron to run. If you later add scheduled work, run it as a
separate container from the API image.

### File storage

- Uploaded resumes are stored on the local file system under `STORAGE_DIR` (`/data/storage`
  in the container, on the `storage` volume). Keys are generated by the server
  (`resumes/<profile>/<uuid>.<ext>`); file names from users are never used as paths.
- Generated PDFs and DOCX are rendered on demand and never stored.
- The volume must persist and be backed up (step 7). `/health/ready` fails if it isn't
  writable.
- Running several API hosts needs shared storage (e.g. a network volume). An object-storage
  backend (S3) can be added behind the `FileStorage` interface in `app/resumes/storage.py`.

### Security checklist

- [ ] `.env` is readable only by the deploying user (`chmod 600`) and never committed.
- [ ] Fresh secrets: `AUTH_PROXY_SECRET`, `POSTGRES_PASSWORD`, login password.
- [ ] Only ports 80/443 are open; the database has no published port.
- [ ] `app.check --production` passes after every deploy.
- [ ] Backups run, are encrypted, and a restore has been tested.
- [ ] Review [security.md](security.md) for the threat model and limitations.

## Run the whole system locally from a fresh clone

Prerequisites: **Git**, **Node.js 20.9+** (npm), **uv 0.5+** (installs Python 3.12 if
needed), and **Docker** for PostgreSQL. Without Docker, use a local PostgreSQL 16+ with
pgvector (see below).

```bash
# 1. Get the code and install dependencies
git clone https://github.com/SRBaggari/CareerPilot.git
cd CareerPilot
npm install
(cd apps/api && uv sync)
(cd apps/api && uv run playwright install chromium)   # for browser-assisted applications

# 2. Configure (defaults work as-is)
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local

# 3. Database: PostgreSQL 17 + pgvector on 127.0.0.1:5432, then the schema
npm run db:up
npm run db:migrate

# 4. Run (three terminals)
npm run dev:api        # API        http://localhost:8000   (docs at /docs)
npm run dev:web        # web app    http://localhost:3000
npm run mock-site      # mock application site http://127.0.0.1:8790 (optional)

# 5. Check
npm run check:api      # configuration, database, pgvector, migrations, storage, browser
```

Open http://localhost:3000. In development every request from your computer acts as
`DEV_USER_EMAIL`. Without AI keys everything works with the rule-based components; set
`ANTHROPIC_API_KEY` (and optionally `EMBEDDING_PROVIDER=voyage` with `VOYAGE_API_KEY`) in
`apps/api/.env` to use Claude and semantic search.

**Without Docker:** install PostgreSQL 16+ and pgvector, then create the databases:

```sql
CREATE USER careerpilot WITH PASSWORD 'careerpilot';
CREATE DATABASE careerpilot OWNER careerpilot;
CREATE DATABASE careerpilot_test OWNER careerpilot;
\c careerpilot
CREATE EXTENSION vector;
\c careerpilot_test
CREATE EXTENSION vector;
```

Then point `DATABASE_URL` in `apps/api/.env` at it and run `npm run db:migrate`.

**Tests:**

```bash
npm test --workspace web                                           # frontend
(cd apps/api && TEST_DATABASE_URL=postgresql+asyncpg://careerpilot:careerpilot@localhost:5432/careerpilot_test uv run pytest)
E2E_ADMIN_DB=postgresql://careerpilot:careerpilot@localhost:5432/postgres npm run test:e2e
npm run typecheck && npm run lint && npm run build
```

`npm run db:up` creates `careerpilot_test` automatically. The browser end-to-end tests
need a role allowed to create databases.
