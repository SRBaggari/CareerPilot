"""Drive the browser. Two separate phases, each in a fresh browser that is closed afterwards.

Prepare: open the page, stop at any access control, detect the fields, fill them from the
approved materials, read the form back, and build the review. Every non-GET request is
blocked, so nothing can be submitted, not even by a script on the page. A page that tries
to send anything is treated as unsafe and the run stops.

Submit (only after the candidate confirmed a review by its hash): fill the form again,
read it back, and press submit only if the form reads back to exactly the confirmed review.
The only request allowed to send data is the form's own submission, after the click.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    FilePayload,
    Page,
    Route,
    WebSocketRoute,
    async_playwright,
)

from app.automation.adapters.base import DetectedForm, SiteAdapter, Unsupported, host_of
from app.automation.guard import Blocked, check_page
from app.automation.models import AuditActor
from app.automation.plan import FillPlan, Materials, Problem, build_review, plan_fill
from app.automation.review import Destination, Review, review_hash
from app.core.config import Settings

logger = logging.getLogger(__name__)

_READ_BACK = """async (ids) => {
  const out = {};
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (el.type === "file") {
      const file = el.files && el.files[0];
      if (!file) { out[id] = null; continue; }
      let sha256 = null;
      if (window.crypto && crypto.subtle) {
        const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
        sha256 = Array.from(new Uint8Array(digest))
          .map(b => b.toString(16).padStart(2, "0")).join("");
      }
      out[id] = {name: file.name, size: file.size, sha256};
    } else if (el.tagName === "SELECT") {
      const option = el.options[el.selectedIndex];
      out[id] = option && option.value ? option.textContent.trim() : "";
    } else {
      out[id] = el.value;
    }
  }
  return out;
}"""


@dataclass(frozen=True)
class Event:
    actor: AuditActor
    action: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))  # when it happened


@dataclass
class Outcome:
    events: list[Event] = field(default_factory=list)
    stop_reason: str | None = None
    failed: bool = False  # an unexpected error rather than a deliberate stop
    problems: list[Problem] = field(default_factory=list)
    review: Review | None = None
    review_hash: str | None = None
    confirmation_reference: str | None = None
    blocked: list[str] = field(default_factory=list)  # requests the gate refused

    def log(self, action: str, message: str, **detail: Any) -> None:
        self.events.append(Event(AuditActor.AUTOMATION, action, message, detail))

    def stop(self, action: str, reason: str, **detail: Any) -> None:
        self.stop_reason = reason
        self.log(action, reason, **detail)


class _Session:
    """One open page, with the request gate."""

    def __init__(self, page: Page, outcome: Outcome, origin: str) -> None:
        self.page = page
        self.outcome = outcome
        self.origin = origin  # the destination site; nothing else is ever contacted
        self.allowed_submission: str | None = None  # the form action, once armed
        self.external: set[str] = set()

    async def gate(self, route: Route) -> None:
        request = route.request
        same_site = _origin(request.url) == self.origin
        main_navigation = request.is_navigation_request() and request.frame == self.page.main_frame
        if request.method in ("GET", "HEAD") and same_site:
            await route.continue_()
            return
        if request.method in ("GET", "HEAD") and not main_navigation:
            # Other sites (trackers, CDNs, internal addresses) are never contacted, so a
            # page script can't send the filled form out or reach the server's network.
            host = urlparse(request.url).netloc
            if host not in self.external:
                self.external.add(host)
                self.outcome.log(
                    "blocked_external",
                    f"Didn't load content from {host or 'another site'}.",
                    host=host,
                )
            await route.abort("blockedbyclient")
            return
        target = _clean(request.url)
        if (
            self.allowed_submission is not None
            and target == self.allowed_submission
            and main_navigation
        ):
            self.allowed_submission = None  # exactly one submission: the form's own
            await route.continue_()
            return
        self.outcome.blocked.append(f"{request.method} {target}")
        self.outcome.log(
            "blocked_request",
            f"Blocked a {request.method} request the page tried to send.",
            method=request.method,
            url=target,
        )
        await route.abort("blockedbyclient")

    async def popup(self, page: Page) -> None:
        """A page that opens another window is unsafe; the window is closed at once."""
        if page == self.page:
            return
        self.outcome.blocked.append(f"POPUP {_clean(page.url)}")
        self.outcome.log("blocked_popup", "The page tried to open another window.")
        await page.close()

    def popup_attempt(self, source: object, target: object = "") -> None:
        """Reported by the page guard: an attempt to open a window or send a form to one."""
        self.outcome.blocked.append(f"POPUP {str(target)[:200]}")
        self.outcome.log("blocked_popup", "The page tried to open another window.")


_PAGE_GUARD = """(() => {
  const report = (target) => { try { window.__careerpilotPopup(String(target || "")); } catch {} };
  window.open = function (url) { report(url); return null; };
  const submit = HTMLFormElement.prototype.submit;
  const opensWindow = (form) => form.target && !["", "_self"].includes(form.target);
  HTMLFormElement.prototype.submit = function () {
    if (opensWindow(this)) { report("form:" + this.target); return; }
    return submit.call(this);
  };
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (opensWindow(form)) { event.preventDefault(); report("form:" + form.target); }
  }, true);
})();"""


def _origin(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _clean(url: str) -> str:
    """A URL without its query or fragment (which may carry data or tokens) for logs."""
    return url.split("#")[0].split("?")[0]


def _by_id(field_id: str) -> str:
    """A selector for an element id the page chose (escaped: never interpreted as CSS)."""
    escaped = field_id.replace("\\", "\\\\").replace('"', '\\"')
    return f'[id="{escaped}"]'


async def _close_websocket(ws: WebSocketRoute) -> None:
    await ws.close()


@asynccontextmanager
async def _browser(settings: Settings, outcome: Outcome, url: str) -> AsyncIterator[_Session]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=settings.automation_headless,
            # No popups or new windows at all: a request from a brand-new window can race
            # the request gate before it attaches, so new windows are refused outright.
            args=["--block-new-web-contents"],
        )
        try:
            context = await browser.new_context(
                accept_downloads=False,
                service_workers="block",  # their requests would bypass the gate
            )
            context.set_default_timeout(settings.automation_timeout_ms)
            page = await context.new_page()
            session = _Session(page, outcome, _origin(url))
            # The gate covers every page in the context (popups too) and every request.
            await context.route("**/*", session.gate)
            await context.route_web_socket("**/*", _close_websocket)
            context.on("page", session.popup)
            # Runs before any page script: opening windows (or sending a form into one) is
            # refused and reported, so such a page is treated as unsafe.
            await context.expose_binding("__careerpilotPopup", session.popup_attempt)
            await context.add_init_script(_PAGE_GUARD)
            yield session
        finally:
            await browser.close()
            outcome.log("browser_closed", "Closed the browser.")


async def _open(
    session: _Session, url: str, adapter: SiteAdapter, outcome: Outcome
) -> DetectedForm | None:
    outcome.log("open_page", f"Opened {host_of(url)}.", url=_clean(url))
    response = await session.page.goto(url, wait_until="load")
    if _origin(session.page.url) != session.origin:
        outcome.stop(
            "redirected",
            f"The page sent the browser to another site ({host_of(session.page.url)}). "
            "CareerPilot only fills pages on the site you approved, so it stopped.",
            url=_clean(session.page.url),
        )
        return None
    try:
        await check_page(session.page, response)
        form = await adapter.detect(session.page)
    except Blocked as exc:
        outcome.stop("access_control", str(exc), url=_clean(session.page.url))
        return None
    except Unsupported as exc:
        outcome.stop("unsupported_form", str(exc), url=_clean(session.page.url))
        return None
    outcome.log(
        "detected_fields",
        f"Found {len(form.fields)} supported fields.",
        fields=[f.field_id for f in form.fields],
    )
    return form


async def _fill(session: _Session, form: DetectedForm, plan: FillPlan, outcome: Outcome) -> None:
    page = session.page
    kinds = {f.field_id: f for f in form.fields}
    for field_id, value in plan.values.items():
        control = page.locator(_by_id(field_id))
        if kinds[field_id].kind == "select":
            await control.select_option(label=value)
        else:
            await control.fill(value)
    outcome.log(
        "filled_fields",
        f"Filled {len(plan.values)} fields.",
        fields=sorted(plan.values),
    )
    for field_id, document in plan.uploads.items():
        payload: FilePayload = {
            "name": document.file_name,
            "mimeType": "application/pdf",
            "buffer": document.data,
        }
        await page.locator(_by_id(field_id)).set_input_files(payload)
        label = "resume" if document.kind == "resume" else "cover letter"
        outcome.log(
            f"attached_{document.kind}",
            f"Attached your approved {label} (version {document.version}).",
            file_name=document.file_name,
            sha256=document.sha256,
        )
    if plan.answers:
        outcome.log(
            "filled_answers",
            f"Filled {len(plan.answers)} approved answers.",
            answer_ids=[str(a.answer_id) for a in plan.answers.values()],
        )


async def _fill_and_review(
    session: _Session,
    url: str,
    adapter: SiteAdapter,
    materials: Materials,
    inputs: dict[str, str],
    outcome: Outcome,
) -> tuple[DetectedForm, Review] | None:
    form = await _open(session, url, adapter, outcome)
    if form is None:
        return None
    plan = plan_fill(form, materials, inputs)
    if plan.problems:
        outcome.problems = plan.problems
        outcome.log(
            "needs_input",
            f"{len(plan.problems)} field{'s' if len(plan.problems) != 1 else ''} need"
            f"{'' if len(plan.problems) != 1 else 's'} you before anything can be filled.",
            fields=[p.field_id for p in plan.problems],
        )
        return None
    await _fill(session, form, plan, outcome)
    read_back = await session.page.evaluate(_READ_BACK, [f.field_id for f in form.fields])
    destination = Destination(
        url=url, host=host_of(url), adapter=adapter.name, form_action=form.action_url
    )
    review, mismatches = build_review(form, plan, read_back, destination)
    if mismatches:
        outcome.stop(
            "fill_mismatch",
            "The form didn't keep some of the values CareerPilot entered ("
            + ", ".join(mismatches)
            + "). Nothing was submitted; apply on the site yourself.",
            fields=mismatches,
        )
        return None
    outcome.log("read_back", "Read the filled form back to build the review.")
    return form, review


def _same_site(a: str, b: str) -> bool:
    return urlparse(a).netloc == urlparse(b).netloc


async def prepare(
    url: str,
    adapter: SiteAdapter,
    materials: Materials,
    inputs: dict[str, str],
    settings: Settings,
) -> Outcome:
    outcome = Outcome()
    try:
        async with _browser(settings, outcome, url) as session:
            result = await _fill_and_review(session, url, adapter, materials, inputs, outcome)
            # Give page scripts a moment: a page that tries to send the form by itself is
            # blocked, and treated as unsafe.
            await session.page.wait_for_timeout(250)
            if result is not None and outcome.stop_reason is None:
                form, review = result
                if not _same_site(form.action_url, url):
                    outcome.stop(
                        "unsupported_form",
                        "The form sends to a different site ("
                        + host_of(form.action_url)
                        + "), so CareerPilot won't submit it.",
                    )
                else:
                    outcome.review, outcome.review_hash = review, review_hash(review)
                    outcome.log(
                        "paused_for_review",
                        "Paused before submitting. Nothing has been submitted.",
                        review_hash=outcome.review_hash,
                    )
    except PlaywrightError as exc:
        outcome.failed = True
        outcome.stop("error", _error_message(exc))
    except Exception:  # an unexpected page or bug: fail safely, never half-done
        logger.exception("Browser preparation failed")
        outcome.failed = True
        outcome.stop("error", "Something unexpected went wrong. Nothing was submitted.")
    _stop_if_unsafe(outcome)
    return outcome


async def submit(
    url: str,
    adapter: SiteAdapter,
    materials: Materials,
    inputs: dict[str, str],
    confirmed_hash: str,
    settings: Settings,
) -> Outcome:
    outcome = Outcome()
    try:
        async with _browser(settings, outcome, url) as session:
            result = await _fill_and_review(session, url, adapter, materials, inputs, outcome)
            if outcome.blocked:
                return outcome
            if result is None or outcome.stop_reason is not None:
                if outcome.stop_reason is None:
                    outcome.stop(
                        "form_changed",
                        "The form now asks for something that isn't in the review you "
                        "confirmed. Nothing was submitted; prepare it again.",
                    )
                return outcome
            form, review = result
            current = review_hash(review)
            if current != confirmed_hash:
                outcome.review, outcome.review_hash = review, current
                outcome.stop(
                    "form_changed",
                    "The filled form no longer matches the review you confirmed (the site or "
                    "your materials changed). Nothing was submitted; review it again.",
                    confirmed=confirmed_hash,
                    current=current,
                )
                return outcome
            outcome.log(
                "verified_review",
                "The filled form matches the review you confirmed.",
                review_hash=current,
            )
            session.allowed_submission = form.action_url.split("#")[0].split("?")[0]
            outcome.log("submit_clicked", "Pressed submit, as you confirmed.")
            try:
                reference = await adapter.submit(session.page)
            except Unsupported as exc:
                outcome.stop("no_confirmation", str(exc))
                return outcome
            outcome.review, outcome.review_hash = review, current
            outcome.confirmation_reference = reference
            outcome.log(
                "submitted",
                f"The site confirmed the submission (reference {reference}).",
                reference=reference,
            )
    except PlaywrightError as exc:
        outcome.failed = True
        clicked = any(e.action == "submit_clicked" for e in outcome.events)
        outcome.stop("error", _error_message(exc, after_submit=clicked))
    except Exception:  # an unexpected page or bug: fail safely
        logger.exception("Browser submission failed")
        outcome.failed = True
        clicked = any(e.action == "submit_clicked" for e in outcome.events)
        outcome.stop(
            "error",
            "Something unexpected went wrong after pressing submit: check the site before "
            "trying again."
            if clicked
            else "Something unexpected went wrong. Nothing was submitted.",
        )
    if outcome.confirmation_reference is None:
        _stop_if_unsafe(outcome)
    return outcome


def _stop_if_unsafe(outcome: Outcome) -> None:
    """A page that tried to send data by itself is unsafe, whatever else happened."""
    if outcome.blocked:
        outcome.failed = False
        outcome.review = outcome.review_hash = None
        outcome.problems = []
        outcome.stop(
            "unsafe_page",
            "The page tried to send data by itself while CareerPilot was filling it. "
            "CareerPilot blocked it and stopped; nothing was submitted. Apply on the site "
            "yourself if you trust it.",
            requests=outcome.blocked,
        )


def _error_message(exc: PlaywrightError, *, after_submit: bool = False) -> str:
    first = (exc.message or str(exc)).strip().splitlines()[0][:300]
    if after_submit:
        return (
            f"The browser hit an error after pressing submit ({first}). The site may or may "
            "not have received the application: check the site before trying again."
        )
    return f"The browser hit an error ({first}). Nothing was submitted; try again later."
