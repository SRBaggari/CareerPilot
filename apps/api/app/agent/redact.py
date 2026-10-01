"""Keep secrets out of the agent's logs.

Everything written to the execution log passes through ``redact``: the configured secret
values themselves (API keys, the database password), and anything that looks like a key,
token or password, are replaced with ``[REDACTED]``. Summaries are also truncated.
"""

import re
from urllib.parse import urlparse

from pydantic import SecretStr

from app.core.config import Settings

REDACTED = "[REDACTED]"
LIMIT = 500
_TOKENS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bpa-[A-Za-z0-9_\-]{16,}"),  # Voyage AI
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]+")
_ASSIGNMENT = re.compile(
    r"(?i)\b((?:api[_-]?key|secret|token|password|passwd)\"?\s*[:=]\s*\"?)[^\s\"',;}]+"
)
_DSN_PASSWORD = re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+:)[^@\s]+(@)")
_SECRET_NAMES = ("key", "secret", "token", "password")


def secrets_of(settings: Settings) -> list[str]:
    """The secret values configured in ``settings`` (never logged)."""
    found: list[str] = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not isinstance(value, str):
            continue
        if name == "database_url":
            password = urlparse(value).password
            if password and len(password) >= 4:
                found.append(password)
        elif len(value) >= 8 and any(part in name.lower() for part in _SECRET_NAMES):
            found.append(value)
    return found


def redact(text: object, secrets: list[str] | None = None, limit: int = LIMIT) -> str:
    value = str(text)
    for secret in sorted(secrets or [], key=len, reverse=True):
        value = value.replace(secret, REDACTED)
    for token in _TOKENS:
        value = token.sub(REDACTED, value)
    value = _BEARER.sub(lambda m: m.group(1) + REDACTED, value)
    value = _ASSIGNMENT.sub(lambda m: m.group(1) + REDACTED, value)
    value = _DSN_PASSWORD.sub(lambda m: m.group(1) + REDACTED + m.group(2), value)
    return value if len(value) <= limit else value[: limit - 1] + "…"
