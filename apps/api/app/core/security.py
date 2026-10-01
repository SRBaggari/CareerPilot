"""HTTP-level protections (pure ASGI middleware, so they also cover streamed bodies).

- ``BodySizeLimit``: refuse request bodies over a limit before they are read or parsed
  (multipart uploads are otherwise spooled in full before the route sees them).
- ``CrossSiteGuard``: refuse state-changing requests sent by a browser from another site.
  A page on another origin can send "simple" requests (no-body POSTs, multipart forms)
  without a CORS preflight; CORS only hides the response. Browsers always send ``Origin``
  (and ``Sec-Fetch-Site``) on such requests, so they are checked against the allowed
  origins. Non-browser clients (no ``Origin``) are unaffected.
- ``SecurityHeaders``: no sniffing, no framing, no caching of personal data, no referrer.
"""

import json
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def _refuse(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _header(scope, b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _refuse(send, 413, "The request is too large.")
            return
        received = 0
        refused = False  # we answered 413; the app's own response is discarded
        started = False

        async def limited() -> Message:
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    refused = True
                    if not started:
                        await _refuse(send, 413, "The request is too large.")
                    return {"type": "http.disconnect"}  # the app stops reading
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if refused:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited, tracking_send)
        except Exception:
            if not refused:
                raise


class CrossSiteGuard:
    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed = {o.rstrip("/").lower() for o in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            origin = _header(scope, b"origin")
            fetch_site = _header(scope, b"sec-fetch-site")
            if origin is not None and origin.rstrip("/").lower() not in self.allowed:
                await _refuse(send, 403, "Cross-site request refused.")
                return
            if origin is None and fetch_site == "cross-site":
                await _refuse(send, 403, "Cross-site request refused.")
                return
        await self.app(scope, receive, send)


SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cross-origin-resource-policy", b"same-site"),
    (b"cache-control", b"no-store"),
]


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS if k not in present]
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        await self.app(scope, receive, with_headers)
