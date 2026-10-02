"""Logging for production: structured lines, a request ID on everything, no personal data.

- ``LOG_FORMAT=json`` writes one JSON object per line (for log collectors);
  ``text`` writes readable lines (development).
- Every request gets an ID (the incoming ``X-Request-ID`` when it is well formed, else a new
  one), returned in the ``X-Request-ID`` response header and attached to every log line
  written while handling it, so a user-visible error can be traced to its log entry.
- One access line per request: method, path (never the query string), status, duration.
  Bodies, headers and query strings are never logged.
- Third-party debug logs (which would contain prompts and SQL parameters) are capped at
  WARNING.
"""

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
access_log = logging.getLogger("careerpilot.access")
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_NOISY = ("anthropic", "httpx", "httpx2", "httpcore", "sqlalchemy.engine", "uvicorn.access")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key in ("method", "path", "status", "duration_ms"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        JsonFormatter()
        if settings.log_format == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
    )
    handler.set_name("careerpilot")
    root = logging.getLogger()
    # Replace only our own handler (calling this twice must not duplicate lines, and other
    # tools' handlers, such as a test runner's, stay).
    root.handlers[:] = [h for h in root.handlers if h.get_name() != "careerpilot"]
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    for name in _NOISY:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))


class RequestContext:
    """Assigns the request ID and writes the access line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers", [])).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _VALID_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            # One generic answer for unexpected errors: the details (which may include SQL,
            # paths or personal data) go to the log, tied to the request ID the user sees.
            logging.getLogger("careerpilot.errors").exception("Unhandled error")
            status = 500
            body = json.dumps(
                {
                    "detail": "Something went wrong on our side. Please try again; if it keeps "
                    f"happening, quote reference {rid}.",
                    "request_id": rid,
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"x-request-id", rid.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
        finally:
            duration = round((time.perf_counter() - started) * 1000, 1)
            access_log.info(
                "%s %s %s %sms",
                scope["method"],
                scope["path"],
                status,
                duration,
                extra={
                    "method": scope["method"],
                    "path": scope["path"],  # never the query string
                    "status": status,
                    "duration_ms": duration,
                },
            )
            request_id.reset(token)
