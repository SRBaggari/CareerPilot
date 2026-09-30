"""Access rules every job source must follow.

- A provider declares a ``SourcePolicy``: how it is allowed to get jobs. There is no
  "scraper" kind: only official APIs, feeds published for automated use, and the
  development mock.
- ``validate_policy`` refuses sources whose terms don't permit automated access, feeds
  that don't honour robots.txt, and APIs whose credentials aren't configured.
- ``check_response`` turns a refusal from a source (authentication required, forbidden,
  rate limited, a CAPTCHA or bot challenge) into ``AccessDenied``. Callers stop there:
  nothing retries around, logs in to, solves, or otherwise bypasses an access control.
- ``robots_allows`` checks a URL against a robots.txt, for feed providers.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.robotparser import RobotFileParser

from app.core.config import Settings

USER_AGENT = "CareerPilotBot/0.1 (+https://github.com/SRBaggari/CareerPilot)"


class AccessKind(StrEnum):
    OFFICIAL_API = "official_api"  # a documented API the source offers for this purpose
    PUBLIC_FEED = "public_feed"  # a feed published for automated consumption (RSS/JSON)
    MOCK = "mock"  # built-in sample data for development and tests


@dataclass(frozen=True)
class SourcePolicy:
    kind: AccessKind
    description: str
    terms_url: str | None = None
    automated_access_permitted: bool = False  # the source's terms allow programmatic access
    respects_robots: bool = True
    credential_setting: str | None = None  # the Settings field holding the API key, if any
    min_request_interval_seconds: float = 1.0


@dataclass
class SourceValidation:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class AccessDenied(Exception):
    """A source refused access. The request is abandoned, never worked around."""


def validate_policy(policy: SourcePolicy, settings: Settings) -> SourceValidation:
    reasons: list[str] = []
    if policy.kind == AccessKind.MOCK:
        if settings.app_env == "production":
            reasons.append("The mock provider is sample data for development only.")
        return SourceValidation(not reasons, reasons)
    if not policy.automated_access_permitted:
        reasons.append("The source's terms don't permit automated access.")
    if not policy.terms_url:
        reasons.append("The source's terms of use must be recorded.")
    if policy.kind == AccessKind.PUBLIC_FEED and not policy.respects_robots:
        reasons.append("Feed providers must honour robots.txt.")
    if policy.credential_setting:
        value: Any = getattr(settings, policy.credential_setting, None)
        secret = value.get_secret_value() if hasattr(value, "get_secret_value") else value
        if not secret:
            reasons.append(
                f"Set {policy.credential_setting.upper()} to use this source's official API."
            )
    if policy.min_request_interval_seconds < 0.5:
        reasons.append("Requests must be rate limited (at least 0.5 s apart).")
    return SourceValidation(not reasons, reasons)


_CHALLENGE = re.compile(
    r"captcha|recaptcha|hcaptcha|cf-challenge|challenge-platform|are you a robot|"
    r"verify you are human|unusual traffic|access denied",
    re.IGNORECASE,
)


def check_response(status: int, headers: Mapping[str, str], body: str) -> None:
    """Raise ``AccessDenied`` if a source is refusing or challenging the request."""
    if status in (401, 407):
        raise AccessDenied("The source requires authentication; CareerPilot won't bypass it.")
    if status == 403:
        raise AccessDenied("The source refused access; CareerPilot won't bypass it.")
    if status == 429:
        wait = headers.get("Retry-After") or headers.get("retry-after")
        raise AccessDenied(
            "The source is rate limiting requests" + (f" (retry after {wait}s)." if wait else ".")
        )
    if _CHALLENGE.search(body[:20000]):
        raise AccessDenied(
            "The source showed a CAPTCHA or bot challenge; CareerPilot doesn't solve or evade them."
        )


def robots_allows(robots_txt: str, url: str, user_agent: str = USER_AGENT) -> bool:
    """Whether robots.txt permits ``user_agent`` to fetch ``url``."""
    parser = RobotFileParser()
    parser.parse(robots_txt.splitlines())
    return parser.can_fetch(user_agent, url)
