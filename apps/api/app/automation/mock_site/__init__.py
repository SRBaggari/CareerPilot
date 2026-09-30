"""A mock job application website for developing and testing browser assistance.

Run it with ``uv run python -m app.automation.mock_site`` (http://127.0.0.1:8790).

Pages (``{slug}`` is any job slug):

- ``/jobs/{slug}/apply``: a supported application form
- ``/captcha/{slug}/apply``: the same form behind a CAPTCHA
- ``/secure/{slug}/apply``: redirects to a login page
- ``/blocked/{slug}/apply``: 403 Forbidden
- ``/ratelimited/{slug}/apply``: 429 Too Many Requests
- ``/changed/{slug}/apply``: a form with an unrecognised layout
- ``/autosubmit/{slug}/apply``: a form whose script tries to submit itself on load

Every submission is recorded in memory: ``GET /__submissions`` lists them,
``DELETE /__submissions`` clears them, and ``POST /__questions/{slug}`` changes the
questions a form asks (to simulate a form changing between review and submission).
"""

import hashlib
import html
import itertools
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

DEFAULT_QUESTIONS = [
    ("q1", "Why are you interested in this role?", True),
    ("q2", "Describe your experience with Python.", False),
]

app = FastAPI(title="Mock application site", docs_url=None, redoc_url=None, openapi_url=None)
SUBMISSIONS: list[dict[str, Any]] = []
QUESTIONS: dict[str, list[tuple[str, str, bool]]] = {}
_references = itertools.count(1001)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        f"</head><body><h1>{html.escape(title)}</h1>{body}</body></html>",
        status_code=status,
    )


def _field(field_id: str, label: str, required: bool, control: str) -> str:
    star = " *" if required else ""
    return (
        f"<div class='field' data-required='{str(required).lower()}'>"
        f"<label for='{field_id}'>{html.escape(label)}{star}</label>{control}</div>"
    )


def _input(field_id: str, required: bool, kind: str = "text") -> str:
    return (
        f"<input id='{field_id}' name='{field_id}' type='{kind}'{' required' if required else ''}>"
    )


def application_form(slug: str, extra: str = "") -> str:
    questions = QUESTIONS.get(slug, DEFAULT_QUESTIONS)
    personal = "".join(
        _field(fid, label, req, _input(fid, req, kind))
        for fid, label, req, kind in (
            ("first_name", "First name", True, "text"),
            ("last_name", "Last name", True, "text"),
            ("email", "Email", True, "email"),
            ("phone", "Phone", False, "tel"),
            ("location", "Location", False, "text"),
            ("linkedin", "LinkedIn profile", False, "url"),
        )
    )
    documents = _field(
        "resume",
        "Resume (PDF)",
        True,
        "<input id='resume' name='resume' type='file' accept='.pdf' required>",
    ) + _field(
        "cover_letter",
        "Cover letter (PDF)",
        False,
        "<input id='cover_letter' name='cover_letter' type='file' accept='.pdf'>",
    )
    asked = "".join(
        _field(
            qid,
            label,
            req,
            f"<textarea id='{qid}' name='{qid}'{' required' if req else ''}></textarea>",
        )
        for qid, label, req in questions
    )
    additional = _field(
        "work_authorization",
        "Are you authorized to work in India?",
        True,
        "<select id='work_authorization' name='work_authorization' required>"
        "<option value=''>Select…</option><option>Yes</option><option>No</option></select>",
    ) + _field(
        "referral_source", "How did you hear about us?", False, _input("referral_source", False)
    )
    return (
        f"<form id='application' method='post' action='/jobs/{html.escape(slug)}/submit' "
        "enctype='multipart/form-data' data-careerpilot-form='v1'>"
        f"<section id='personal'>{personal}</section>"
        f"<section id='documents'>{documents}</section>"
        f"<section id='questions'>{asked}</section>"
        f"<section id='additional'>{additional}</section>"
        f"{extra}<button type='submit' id='submit'>Submit application</button></form>"
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return _page("Mock Careers", "<p>A mock application site for CareerPilot tests.</p>")


@app.get("/jobs/{slug}/apply", response_class=HTMLResponse)
async def apply(slug: str) -> HTMLResponse:
    return _page(f"Apply: {slug}", application_form(slug))


@app.get("/captcha/{slug}/apply", response_class=HTMLResponse)
async def apply_with_captcha(slug: str) -> HTMLResponse:
    widget = (
        "<div class='g-recaptcha' data-sitekey='mock'></div>"
        "<p>Please verify you are human before submitting.</p>"
    )
    return _page(f"Apply: {slug}", application_form(slug, widget))


@app.get("/secure/{slug}/apply")
async def apply_requires_login(slug: str) -> RedirectResponse:
    return RedirectResponse(f"/login?next=/secure/{slug}/apply", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login() -> HTMLResponse:
    return _page(
        "Sign in",
        "<form method='post'><label for='u'>Email</label><input id='u'>"
        "<label for='p'>Password</label><input id='p' type='password'>"
        "<button>Sign in</button></form>",
    )


@app.get("/blocked/{slug}/apply", response_class=HTMLResponse)
async def apply_blocked(slug: str) -> HTMLResponse:
    return _page("Access denied", "<p>Automated access is not permitted.</p>", status=403)


@app.get("/ratelimited/{slug}/apply", response_class=HTMLResponse)
async def apply_rate_limited(slug: str) -> HTMLResponse:
    response = _page("Too many requests", "<p>Slow down.</p>", status=429)
    response.headers["Retry-After"] = "120"
    return response


@app.get("/changed/{slug}/apply", response_class=HTMLResponse)
async def apply_changed_layout(slug: str) -> HTMLResponse:
    return _page(
        f"Apply: {slug}",
        "<form method='post' action='/nowhere'>"
        "<label for='g'>Given name</label><input id='g'>"
        "<button>Send</button></form>",
    )


@app.get("/autosubmit/{slug}/apply", response_class=HTMLResponse)
async def apply_that_submits_itself(slug: str) -> HTMLResponse:
    script = (
        "<script>window.addEventListener('load', () => "
        "setTimeout(() => HTMLFormElement.prototype.submit.call("
        "document.getElementById('application')), 50));</script>"
    )
    return _page(f"Apply: {slug}", application_form(slug) + script)


@app.post("/jobs/{slug}/submit", response_class=HTMLResponse)
async def submit(slug: str, request: Request) -> HTMLResponse:
    fields: dict[str, str] = {}
    files: dict[str, dict[str, Any]] = {}
    async with request.form() as form:
        for key, value in form.multi_items():
            if isinstance(value, str):
                fields[key] = value
            else:
                data = await value.read()
                if data:
                    files[key] = {
                        "file_name": value.filename,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
    reference = f"MOCK-{next(_references)}"
    SUBMISSIONS.append({"slug": slug, "reference": reference, "fields": fields, "files": files})
    return _page(
        "Application received",
        f"<p id='confirmation'>Application received. Reference: {reference}</p>",
    )


@app.get("/__submissions")
async def list_submissions() -> JSONResponse:
    return JSONResponse(SUBMISSIONS)


@app.delete("/__submissions")
async def clear_submissions() -> JSONResponse:
    SUBMISSIONS.clear()
    QUESTIONS.clear()
    return JSONResponse({"cleared": True})


@app.post("/__questions/{slug}")
async def set_questions(slug: str, request: Request) -> JSONResponse:
    payload = await request.json()
    QUESTIONS[slug] = [(q["id"], q["label"], bool(q.get("required"))) for q in payload]
    return JSONResponse({"slug": slug, "questions": len(QUESTIONS[slug])})
