"""Browser end-to-end test of the web app (Playwright), against running servers.

Run with ``tests/e2e/run.sh``: it starts an isolated stack (its own database, the mock
application site, the API and a production build of the web app) and sets:

- ``E2E_WEB_URL``  the web app, e.g. http://localhost:3200
- ``E2E_API_URL``  the API, e.g. http://localhost:8200
- ``E2E_SITE_URL`` the mock application site, e.g. http://127.0.0.1:8790

Skipped when they aren't set (for example in the regular test run).
"""

import os
import re
from collections.abc import Iterator
from typing import Any

import httpx2 as httpx
import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.integration import e2e_data as data
from tests.resume_files import make_docx

WEB = os.environ.get("E2E_WEB_URL", "")
API = os.environ.get("E2E_API_URL", "")
SITE = os.environ.get("E2E_SITE_URL", "")
pytestmark = pytest.mark.skipif(not (WEB and API and SITE), reason="E2E servers not configured")


@pytest.fixture(scope="module")
def seeded() -> dict[str, Any]:
    """The candidate, their resume and four analyzed jobs, created through the API."""
    with httpx.Client(base_url=f"{API}/api/v1", timeout=60, headers={"Origin": WEB}) as api:
        api.post(
            "/profile",
            json={"full_name": data.CANDIDATE_NAME, "contact_email": data.CANDIDATE_EMAIL},
        ).raise_for_status()
        resume = api.post(
            "/resumes",
            files={"file": ("aarav.docx", make_docx(data.RESUME_LINES), "application/docx")},
        ).json()
        for s in api.get(f"/profile/suggestions?resume_id={resume['id']}").json():
            api.post(f"/profile/suggestions/{s['id']}/accept", json={}).raise_for_status()
        jobs = {}
        for key, job in data.JOBS.items():
            response = api.post("/jobs/analyze", json=job)
            response.raise_for_status()
            jobs[key] = response.json()
        ai = jobs["ai"]["id"]
        api.post(f"/jobs/{ai}/match").raise_for_status()
        api.post(f"/jobs/{ai}/tailored-resumes").raise_for_status()
        api.post(f"/jobs/{ai}/cover-letters").raise_for_status()
        answers = api.post(
            f"/jobs/{ai}/application-answers",
            json={
                "questions": [
                    "Why are you interested in this role?",
                    "Describe your experience with Python.",
                ]
            },
        ).json()
        for a in answers if isinstance(answers, list) else answers["answers"]:
            api.post(f"/application-answers/{a['id']}/approve").raise_for_status()
        app = api.post("/applications", json={"job_id": ai}).json()
        api.patch(
            f"/applications/{app['id']}",
            json={"application_url": f"{SITE}/jobs/northwind-ai/apply"},
        ).raise_for_status()
        api.post(
            f"/applications/{app['id']}/status", json={"status": "application_prepared"}
        ).raise_for_status()
        api.post("/applications", json={"job_id": jobs["data"]["id"]}).raise_for_status()
        return {"jobs": jobs, "application_id": app["id"]}


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    p = context.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    yield p
    context.close()
    assert errors == [], errors  # no JavaScript errors (including CSP violations that throw)


def test_dashboard_and_jobs(page: Page, seeded: dict[str, Any]) -> None:
    page.goto(WEB)
    expect(page.get_by_role("heading", name="Welcome back, Aarav")).to_be_visible()
    summary = page.get_by_role("region", name="Summary")
    expect(summary.get_by_role("link", name=re.compile(r"Jobs discovered\s*4"))).to_be_visible()
    expect(summary.get_by_role("link", name=re.compile(r"Saved jobs\s*1"))).to_be_visible()

    page.get_by_role("navigation", name="Main").get_by_role("link", name="Jobs", exact=True).click()
    expect(page).to_have_url(re.compile(r"/jobs$"))  # the dashboard links to jobs too
    for job in data.JOBS.values():
        expect(page.get_by_role("link", name=re.compile(job["title"]))).to_be_visible()
    page.get_by_role("link", name=re.compile("AI Engineer Intern")).click()
    expect(page.get_by_role("heading", name="AI Engineer Intern")).to_be_visible()
    expect(
        page.get_by_text("Experience building RAG pipelines or LLM applications").first
    ).to_be_visible()


def test_resume_builder_lists_the_verified_resume(page: Page, seeded: dict[str, Any]) -> None:
    page.goto(f"{WEB}/resumes")
    rows = page.get_by_role("list", name="resumes by job")
    ai_row = rows.get_by_role("listitem").filter(has_text="AI Engineer Intern")
    expect(ai_row).to_contain_text("Verified")
    expect(
        rows.get_by_role("listitem").filter(has_text="Frontend Developer Intern")
    ).to_contain_text("Create")


def test_review_approve_apply_and_track(page: Page, seeded: dict[str, Any]) -> None:
    app_id = seeded["application_id"]

    # Application Review: opening it approves nothing; approval needs the checkbox.
    page.goto(f"{WEB}/review")
    page.get_by_role("link", name=re.compile("Open AI Engineer Intern")).click()
    page.get_by_role("button", name="Mark ready for review").click()
    approve = page.get_by_role("button", name="Approve application")
    expect(approve).to_be_disabled()
    expect(page.get_by_role("region", name="Resume")).to_contain_text("Every claim is verified")
    page.get_by_role("checkbox").check()
    approve.click()
    expect(page.get_by_text(re.compile("Approved by"))).to_be_visible()

    # Mock browser application: needs input, review, explicit confirmation.
    page.goto(f"{WEB}/applications/{app_id}/assist")
    page.get_by_role("button", name="Fill the application for my review").click()
    page.get_by_label(re.compile("Are you authorized")).select_option("Yes")
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_text("Nothing has been submitted yet", exact=False)).to_be_visible(
        timeout=30000
    )
    expect(page.get_by_role("region", name="Personal information")).to_contain_text("Aarav")
    submit = page.get_by_role("button", name="Submit application")
    expect(submit).to_be_disabled()
    page.get_by_role("checkbox").check()
    submit.click()
    expect(page.get_by_text("Submitted after your confirmation.")).to_be_visible(timeout=30000)
    submissions = httpx.get(f"{SITE}/__submissions").json()
    assert len(submissions) == 1 and submissions[0]["fields"]["first_name"] == "Aarav"

    # Tracking.
    page.goto(f"{WEB}/applications/{app_id}")
    expect(page.get_by_label("Status")).to_have_text("Submitted")
    page.goto(WEB)
    expect(page.get_by_role("link", name=re.compile(r"Applications submitted\s*1"))).to_be_visible()


def test_mobile_navigation(browser: Browser, seeded: dict[str, Any]) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(WEB)
    page.get_by_role("button", name="Open menu").click()
    page.get_by_role("dialog", name="Menu").get_by_role("link", name="Application Review").click()
    expect(page.get_by_role("heading", name="Application Review")).to_be_visible()
    width = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert width == 0  # nothing scrolls sideways on a phone
    context.close()
