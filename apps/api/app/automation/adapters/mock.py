"""Adapter for the local mock application site (development and tests only)."""

import re
from typing import Any, cast
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page

from app.automation.adapters.base import DetectedForm, FormField, Section, Unsupported
from app.core.config import Settings

_DETECT = """() => {
  const form = document.querySelector("form[data-careerpilot-form='v1']");
  if (!form) return null;
  const fields = [];
  for (const section of form.querySelectorAll("section")) {
    for (const wrapper of section.querySelectorAll(".field")) {
      const label = wrapper.querySelector("label");
      const control = wrapper.querySelector("input, textarea, select");
      if (!label || !control) continue;
      const kind = control.tagName === "INPUT" ? (control.type || "text")
        : control.tagName.toLowerCase();
      fields.push({
        field_id: control.id, label: label.textContent, section: section.id, kind,
        required: wrapper.dataset.required === "true" || control.required,
        options: control.tagName === "SELECT"
          ? Array.from(control.options).filter(o => o.value).map(o => o.textContent) : [],
      });
    }
  }
  return {action: form.getAttribute("action"), fields};
}"""
_SECTIONS: set[str] = {"personal", "documents", "questions", "additional"}


class MockSiteAdapter:
    name = "mock-site"

    def supports(self, url: str, settings: Settings) -> bool:
        if not settings.automation_mock_site_url or settings.app_env == "production":
            return False
        return urlparse(url).netloc == urlparse(settings.automation_mock_site_url).netloc

    async def detect(self, page: Page) -> DetectedForm:
        found: dict[str, Any] | None = await page.evaluate(_DETECT)
        if not found or not found["fields"]:
            raise Unsupported(
                "The page doesn't have an application form CareerPilot recognises (the site's "
                "layout may have changed). Nothing was filled; apply on the site yourself."
            )
        fields = [
            FormField(
                field_id=f["field_id"],
                label=re.sub(r"\s*\*\s*$", "", " ".join(str(f["label"]).split())),
                section=cast(Section, f["section"] if f["section"] in _SECTIONS else "additional"),
                kind=f["kind"],
                required=bool(f["required"]),
                options=[" ".join(o.split()) for o in f["options"]],
            )
            for f in found["fields"]
        ]
        for name in ("first_name", "last_name", "email", "resume"):
            if not any(f.field_id == name for f in fields):
                raise Unsupported(
                    f"The form is missing an expected field ({name}); it may have changed. "
                    "Nothing was submitted."
                )
        return DetectedForm(action_url=urljoin(page.url, found["action"]), fields=fields)

    async def submit(self, page: Page) -> str:
        async with page.expect_navigation():
            await page.locator("#submit").click()
        confirmation = page.locator("#confirmation")
        if not await confirmation.count():
            raise Unsupported(
                "The site didn't confirm the submission. Check the site before trying again."
            )
        text = " ".join((await confirmation.inner_text()).split())
        match = re.search(r"Reference:\s*(\S+)", text)
        return match.group(1) if match else text[:300]
