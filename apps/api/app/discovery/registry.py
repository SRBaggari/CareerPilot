"""Which job sources are available. Sources are enabled by name (``DISCOVERY_PROVIDERS``)
and must pass ``validate_source`` before they are used."""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import Settings
from app.discovery.provider import JobSourceProvider
from app.discovery.providers.mock import MockJobProvider

# Adapter factories by name. Real providers are added here.
PROVIDERS: dict[str, Callable[[Settings], JobSourceProvider]] = {
    "mock": lambda settings: MockJobProvider(),
}


@dataclass
class SourceStatus:
    name: str
    display_name: str
    access_kind: str | None
    description: str
    terms_url: str | None
    enabled: bool
    reasons: list[str] = field(default_factory=list)


class ProviderRegistry:
    def __init__(
        self,
        providers: list[JobSourceProvider],
        settings: Settings,
        unknown: list[str] | None = None,
    ) -> None:
        self._statuses: list[SourceStatus] = []
        self._enabled: dict[str, JobSourceProvider] = {}
        for provider in providers:
            validation = provider.validate_source(settings)
            self._statuses.append(
                SourceStatus(
                    name=provider.name,
                    display_name=provider.display_name,
                    access_kind=provider.policy.kind.value,
                    description=provider.policy.description,
                    terms_url=provider.policy.terms_url,
                    enabled=validation.allowed,
                    reasons=validation.reasons,
                )
            )
            if validation.allowed:
                self._enabled[provider.name] = provider
        for name in unknown or []:
            self._statuses.append(
                SourceStatus(
                    name=name,
                    display_name=name,
                    access_kind=None,
                    description="",
                    terms_url=None,
                    enabled=False,
                    reasons=["No adapter exists for this source."],
                )
            )

    def statuses(self) -> list[SourceStatus]:
        return list(self._statuses)

    def enabled(self) -> list[JobSourceProvider]:
        return list(self._enabled.values())

    def get(self, name: str) -> JobSourceProvider | None:
        return self._enabled.get(name)


def build_registry(settings: Settings) -> ProviderRegistry:
    names = list(dict.fromkeys(settings.discovery_providers))
    providers = [PROVIDERS[n](settings) for n in names if n in PROVIDERS]
    return ProviderRegistry(providers, settings, [n for n in names if n not in PROVIDERS])
