"""The adapter interface and registry."""

from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlparse

from playwright.async_api import Page

from app.core.config import Settings

Section = Literal["personal", "documents", "questions", "additional"]


class Unsupported(Exception):
    """The page isn't a form this adapter recognises. The run stops with this explanation."""


@dataclass(frozen=True)
class FormField:
    field_id: str
    label: str  # as shown on the page, without the required marker
    section: Section
    kind: str  # text, email, tel, url, textarea, select, file
    required: bool
    options: list[str] = field(default_factory=list)  # for select fields


@dataclass(frozen=True)
class DetectedForm:
    action_url: str
    fields: list[FormField]

    def section(self, name: Section) -> list[FormField]:
        return [f for f in self.fields if f.section == name]


class SiteAdapter(Protocol):
    name: str

    def supports(self, url: str, settings: Settings) -> bool:
        """Whether this adapter handles the site at ``url``."""
        ...

    async def detect(self, page: Page) -> DetectedForm:
        """The supported fields on the page, or raise ``Unsupported``."""
        ...

    async def submit(self, page: Page) -> str:
        """Press submit and return the site's confirmation reference."""
        ...


def adapter_for(url: str, settings: Settings) -> SiteAdapter | None:
    from app.automation.adapters.mock import MockSiteAdapter

    adapters: list[SiteAdapter] = [MockSiteAdapter()]
    for adapter in adapters:
        if adapter.supports(url, settings):
            return adapter
    return None


def host_of(url: str) -> str:
    return urlparse(url).netloc or url
