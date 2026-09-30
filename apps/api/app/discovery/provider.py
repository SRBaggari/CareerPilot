"""The provider abstraction. Each job source is an adapter implementing ``JobSourceProvider``.

To add a real source (e.g. an official job board or ATS API):

1. Subclass ``JobSourceProvider`` with a ``SourcePolicy`` describing the permitted access
   (official API or published feed, terms URL, credential setting).
2. Implement ``search_jobs`` and ``get_job`` against that API only, passing every HTTP
   response through ``access.check_response`` (and ``robots_allows`` for feeds).
3. Implement ``normalize_job`` to map the source's raw posting to ``NormalizedJob``.
4. Register a factory in ``registry.PROVIDERS`` and enable it with ``DISCOVERY_PROVIDERS``.

Nothing else changes: imported jobs go through the same analysis, matching and document
generation as any other job.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from app.core.config import Settings
from app.discovery.access import SourcePolicy, SourceValidation, validate_policy
from app.discovery.models import JobSearchQuery, NormalizedJob
from app.jobs.models import JobSource


class ProviderError(Exception):
    """A provider failed (network, malformed data). Reported per source, never fatal."""


class JobSourceProvider(ABC):
    name: ClassVar[str]  # stable identifier, stored with imported jobs ("mock", "greenhouse")
    display_name: ClassVar[str]
    policy: ClassVar[SourcePolicy]
    job_source: ClassVar[JobSource]  # how imported jobs record their origin

    @abstractmethod
    async def search_jobs(self, query: JobSearchQuery) -> list[NormalizedJob]:
        """Postings matching the query, normalized. May return more than the query asks
        for: the core filters every provider's results the same way."""

    @abstractmethod
    async def get_job(self, source_identifier: str) -> NormalizedJob | None:
        """One posting by the source's own identifier, or None if it doesn't exist."""

    @abstractmethod
    def normalize_job(self, raw: Mapping[str, Any]) -> NormalizedJob:
        """Map one raw posting from the source to ``NormalizedJob``."""

    def validate_source(self, settings: Settings) -> SourceValidation:
        """Whether this source may be used, with reasons if not."""
        return validate_policy(self.policy, settings)
