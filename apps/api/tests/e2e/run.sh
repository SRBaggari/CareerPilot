#!/usr/bin/env bash
# Browser end-to-end test: starts an isolated stack, runs tests/e2e, stops everything.
#
#   E2E_ADMIN_DB=postgresql://postgres@127.0.0.1:5432/postgres tests/e2e/run.sh
#
# Uses a separate database (careerpilot_e2e, recreated each run and dropped afterwards),
# the mock application site on 8790, the API on 8200 and a production build of the web
# app on 3200.
set -euo pipefail
cd "$(dirname "$0")/../.."  # apps/api

ADMIN_DB="${E2E_ADMIN_DB:?set E2E_ADMIN_DB to a PostgreSQL admin URL (database 'postgres')}"
HOST_DB="${ADMIN_DB%/*}"
DB_URL="${HOST_DB/postgresql:/postgresql+asyncpg:}/careerpilot_e2e"
WEB_PORT=3200
API_PORT=8200
SITE_PORT=8790
LOGS="$(mktemp -d)"

stop_port() {  # whatever listens on a port: wrappers such as npx don't pass signals on
  if command -v lsof >/dev/null; then
    lsof -ti "tcp:$1" -sTCP:LISTEN | xargs -r kill 2>/dev/null || true
  elif command -v powershell >/dev/null; then
    powershell -NoProfile -Command \
      "Get-NetTCPConnection -LocalPort $1 -State Listen -EA SilentlyContinue | % { Stop-Process -Id \$_.OwningProcess -Force }" \
      >/dev/null 2>&1 || true
  fi
}
cleanup() {
  for port in "$WEB_PORT" "$API_PORT" "$SITE_PORT"; do stop_port "$port"; done
  psql "$ADMIN_DB" -qc "DROP DATABASE IF EXISTS careerpilot_e2e" >/dev/null 2>&1 || true
  echo "Logs: $LOGS"
}
trap cleanup EXIT

psql "$ADMIN_DB" -qc "DROP DATABASE IF EXISTS careerpilot_e2e" -c "CREATE DATABASE careerpilot_e2e"
psql "$HOST_DB/careerpilot_e2e" -qc "CREATE EXTENSION IF NOT EXISTS vector"
DATABASE_URL="$DB_URL" uv run alembic upgrade head >"$LOGS/migrate.log" 2>&1

echo "Building the web app..."
(cd ../web && NEXT_PUBLIC_API_BASE_URL="http://localhost:$API_PORT" npx next build >"$LOGS/build.log" 2>&1)

uv run python -m app.automation.mock_site >"$LOGS/site.log" 2>&1 &
DATABASE_URL="$DB_URL" DEV_USER_EMAIL=e2e@localhost.dev ANTHROPIC_API_KEY= EMBEDDING_PROVIDER=hash \
  STORAGE_DIR="$LOGS/storage" AUTOMATION_MOCK_SITE_URL="http://127.0.0.1:$SITE_PORT" \
  CORS_ORIGINS="http://localhost:$WEB_PORT" \
  uv run uvicorn app.main:app --port "$API_PORT" >"$LOGS/api.log" 2>&1 &
(cd ../web && npx next start -p "$WEB_PORT" >"$LOGS/web.log" 2>&1) &

for _ in $(seq 1 120); do
  curl -sf "http://localhost:$API_PORT/health" >/dev/null \
    && curl -sf "http://localhost:$WEB_PORT/" >/dev/null \
    && curl -sf "http://127.0.0.1:$SITE_PORT/" >/dev/null && break
  sleep 1
done

E2E_WEB_URL="http://localhost:$WEB_PORT" E2E_API_URL="http://localhost:$API_PORT" \
  E2E_SITE_URL="http://127.0.0.1:$SITE_PORT" uv run pytest tests/e2e -v -p no:logging "$@"
