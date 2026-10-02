# CareerPilot API: FastAPI on Uvicorn, with Chromium for browser-assisted applications.
#
#   docker build -t careerpilot-api apps/api
#   docker build -t careerpilot-api --build-arg INSTALL_BROWSER=false apps/api   # smaller
#
# The same image runs migrations (`alembic upgrade head`) and the preflight
# (`python -m app.check --production`).

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

WORKDIR /app

# Dependencies first (cached unless pyproject.toml or uv.lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
RUN uv sync --frozen --no-dev

# Chromium and its system libraries, for browser-assisted applications. Without it the
# rest of CareerPilot works and assisted runs report that the browser is unavailable.
ARG INSTALL_BROWSER=true
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
      /app/.venv/bin/playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*; \
    fi

RUN useradd --create-home --uid 10001 careerpilot \
    && mkdir -p /data/storage \
    && chown -R careerpilot:careerpilot /data
USER careerpilot

ENV PATH="/app/.venv/bin:$PATH" \
    APP_ENV=production \
    LOG_FORMAT=json \
    STORAGE_DIR=/data/storage

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

# Behind the reverse proxy: trust its X-Forwarded-* headers (the API port is not published).
# Access lines come from CareerPilot's own logging (no query strings), so Uvicorn's are off.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips '*' --no-access-log --timeout-graceful-shutdown 30"]
