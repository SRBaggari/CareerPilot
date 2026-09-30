"""Access controls stop the workflow; they are never worked around.

- Refusals (401, 403, 407, 429) and CAPTCHA / bot-challenge pages: stop.
- Login walls (a sign-in page or a password field): stop. CareerPilot never signs in for
  the candidate.
- During preparation every non-GET request is blocked, so no form can be submitted, even
  by a page script.
"""

import re
from urllib.parse import urlparse

from playwright.async_api import Page, Response

from app.discovery.access import AccessDenied, check_response


class Blocked(Exception):
    """The site put up an access control. The run stops with this explanation."""


_LOGIN_PATH = re.compile(r"/(login|signin|sign-in|auth|sso)\b", re.IGNORECASE)
_CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    ".g-recaptcha",
    ".h-captcha",
    ".cf-turnstile",
    "[class*='captcha']",
    "[id*='captcha']",
)


async def check_page(page: Page, response: Response | None) -> None:
    """Raise ``Blocked`` if the page is refusing, challenging, or asking to sign in."""
    body = await page.content()
    status = response.status if response is not None else 200
    headers = await response.all_headers() if response is not None else {}
    try:
        check_response(status, headers, body)
    except AccessDenied as exc:
        raise Blocked(f"{exc} Open the page yourself if you want to apply there.") from exc
    for selector in _CAPTCHA_SELECTORS:
        if await page.locator(selector).count():
            raise Blocked(
                "The page shows a CAPTCHA. CareerPilot doesn't solve or evade CAPTCHAs; open "
                "the page yourself if you want to apply there."
            )
    if (
        _LOGIN_PATH.search(urlparse(page.url).path)
        or await page.locator("input[type='password']").count()
    ):
        raise Blocked(
            "The site asks you to sign in. CareerPilot never signs in for you or bypasses "
            "authentication; sign in and apply yourself."
        )
