"""Job discovery without a database: the provider contract (run against every registered
adapter), normalization, filters, the access rules, and the registry."""

from datetime import date
from typing import Any

import pytest

from app.core.config import Settings
from app.discovery import filters
from app.discovery.access import (
    AccessDenied,
    AccessKind,
    SourcePolicy,
    check_response,
    robots_allows,
    validate_policy,
)
from app.discovery.models import JobSearchQuery, NormalizedJob, WorkMode
from app.discovery.provider import JobSourceProvider, ProviderError
from app.discovery.providers.mock import MockJobProvider
from app.discovery.registry import PROVIDERS, ProviderRegistry, build_registry
from app.profiles.models import EmploymentType, ExperienceLevel

from .settings_helpers import production_settings, unvalidated

pytestmark = pytest.mark.anyio
DEV = Settings(_env_file=None, app_env="development")
PROD = unvalidated(production_settings(), discovery_providers=["mock"])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- The provider contract, for every registered adapter ----------------------------------


@pytest.fixture(params=sorted(PROVIDERS))
def provider(request: pytest.FixtureRequest) -> JobSourceProvider:
    return PROVIDERS[request.param](DEV)


async def test_every_provider_honours_the_contract(provider: JobSourceProvider) -> None:
    assert provider.name and provider.display_name
    assert provider.policy.kind in AccessKind  # there is no "scraper" kind
    assert provider.validate_source(DEV).allowed or provider.policy.credential_setting
    jobs = await provider.search_jobs(JobSearchQuery())
    assert jobs, "a provider must return postings"
    for job in jobs:
        assert isinstance(job, NormalizedJob)
        assert job.source == provider.name
        assert job.title and job.company and job.description and job.source_identifier
        assert job.url is None or job.url.startswith(("https://", "http://"))
    first = jobs[0]
    assert await provider.get_job(first.source_identifier) == first
    assert await provider.get_job("no-such-posting") is None


def test_the_mock_provider_normalizes_its_own_raw_format() -> None:
    raw = {
        "id": "mock-9",
        "position": " ML Engineer ",
        "org": {"name": "Acme"},
        "where": {"city": "Pune", "country": "India", "arrangement": "REMOTE"},
        "contract": "INTERN",
        "level": "intern",
        "published": "2026-09-01",
        "apply_by": "2026-10-01",
        "link": "https://jobs.example.com/mock/9",
        "body": "Acme is hiring.",
        "tags": ["Python"],
    }
    job = MockJobProvider().normalize_job(raw)
    assert job.model_dump() == {
        "source": "mock",
        "source_identifier": "mock-9",
        "title": "ML Engineer",
        "company": "Acme",
        "location": "Pune, India",
        "url": "https://jobs.example.com/mock/9",
        "description": "Acme is hiring.",
        "employment_type": EmploymentType.INTERNSHIP,
        "work_mode": WorkMode.REMOTE,
        "posted_date": date(2026, 9, 1),
        "deadline": date(2026, 10, 1),
        "skills": ["Python"],
        "experience_level": ExperienceLevel.STUDENT,
    }


def test_malformed_postings_raise_provider_errors() -> None:
    with pytest.raises(ProviderError):
        MockJobProvider().normalize_job({"id": "x"})


def test_normalized_jobs_only_link_to_web_urls() -> None:
    with pytest.raises(ValueError, match="http"):
        NormalizedJob(
            source="mock",
            source_identifier="1",
            title="T",
            company="C",
            description="D",
            url="javascript:alert(1)",
        )


# --- Enrichment and filters ------------------------------------------------------------------


def job(**overrides: Any) -> NormalizedJob:
    base: dict[str, Any] = {
        "source": "mock",
        "source_identifier": "1",
        "title": "Machine Learning Engineer",
        "company": "Acme",
        "location": "Hyderabad, India",
        "description": "Python and SQL.",
        "employment_type": EmploymentType.FULL_TIME,
        "work_mode": WorkMode.HYBRID,
        "posted_date": date(2026, 9, 20),
    }
    return filters.enrich(NormalizedJob(**{**base, **overrides}))


@pytest.mark.parametrize(
    ("title", "employment_type", "level"),
    [
        ("Senior Data Engineer", None, ExperienceLevel.SENIOR),
        ("Lead ML Engineer", None, ExperienceLevel.LEAD),
        ("Junior Developer", None, ExperienceLevel.JUNIOR),
        ("Graduate Software Engineer", None, ExperienceLevel.ENTRY_LEVEL),
        ("ML Intern", None, ExperienceLevel.STUDENT),
        ("Research Assistant", EmploymentType.INTERNSHIP, ExperienceLevel.STUDENT),
        ("Data Engineer", None, None),  # nothing stated: nothing inferred
    ],
)
def test_levels_come_only_from_explicit_wording(
    title: str, employment_type: EmploymentType | None, level: ExperienceLevel | None
) -> None:
    assert job(title=title, employment_type=employment_type).experience_level == level


def test_skills_are_derived_from_the_posting_when_not_supplied() -> None:
    assert set(job(description="Experience with Python, Docker and Kubernetes.").skills) >= {
        "Python",
        "Docker",
        "Kubernetes",
    }


@pytest.mark.parametrize(
    ("query", "kept"),
    [
        ({"role": "ML engineer"}, True),  # abbreviation matches the full title
        ({"role": "machine learning"}, True),
        ({"role": "data scientist"}, False),
        ({"location": "hyderabad"}, True),
        ({"location": "Pune"}, False),
        ({"remote": True}, False),
        ({"remote": False}, True),
        ({"employment_types": [EmploymentType.FULL_TIME]}, True),
        ({"employment_types": [EmploymentType.INTERNSHIP]}, False),
        ({"skills": ["sql", "Rust"]}, True),  # any requested skill, case-insensitive
        ({"skills": ["Rust"]}, False),
        ({"experience_levels": [ExperienceLevel.SENIOR]}, False),  # unknown level excluded
    ],
)
def test_each_filter(query: dict[str, Any], kept: bool) -> None:
    assert filters.matches(job(), JobSearchQuery(**query)) is kept


def test_results_rank_by_requested_skills_then_date() -> None:
    older = job(
        source_identifier="a", description="Python, SQL and Docker.", posted_date=date(2026, 9, 1)
    )
    newer = job(source_identifier="b", description="Python.", posted_date=date(2026, 9, 25))
    ranked = filters.apply([newer, older], JobSearchQuery(skills=["Python", "Docker"]))
    assert [j.source_identifier for j in ranked] == ["a", "b"]
    assert [j.source_identifier for j in filters.apply([older, newer], JobSearchQuery())] == [
        "b",
        "a",
    ]


async def test_filters_over_the_mock_catalogue() -> None:
    catalogue = [filters.enrich(j) for j in await MockJobProvider().search_jobs(JobSearchQuery())]
    remote_interns = filters.apply(
        catalogue, JobSearchQuery(remote=True, employment_types=[EmploymentType.INTERNSHIP])
    )
    assert [j.title for j in remote_interns] == ["AI Research Intern"]
    rag = filters.apply(catalogue, JobSearchQuery(skills=["RAG"]))
    assert {j.title for j in rag} == {"ML Engineer", "AI Research Intern", "NLP Engineer"}


# --- Access rules ---------------------------------------------------------------------------


def policy(**overrides: Any) -> SourcePolicy:
    base: dict[str, Any] = {
        "kind": AccessKind.OFFICIAL_API,
        "description": "An official job board API.",
        "terms_url": "https://example.com/terms",
        "automated_access_permitted": True,
    }
    return SourcePolicy(**{**base, **overrides})


def test_a_permitted_official_api_is_allowed() -> None:
    assert validate_policy(policy(), DEV).allowed


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"automated_access_permitted": False}, "don't permit automated access"),
        ({"terms_url": None}, "terms of use"),
        ({"kind": AccessKind.PUBLIC_FEED, "respects_robots": False}, "robots.txt"),
        ({"credential_setting": "anthropic_api_key"}, "ANTHROPIC_API_KEY"),
        ({"min_request_interval_seconds": 0.1}, "rate limited"),
    ],
)
def test_sources_that_would_break_the_rules_are_refused(
    overrides: dict[str, Any], reason: str
) -> None:
    result = validate_policy(policy(**overrides), DEV)
    assert not result.allowed and any(reason in r for r in result.reasons), result.reasons


def test_configured_credentials_satisfy_the_policy() -> None:
    settings = Settings(_env_file=None, app_env="development", anthropic_api_key="key")
    assert validate_policy(policy(credential_setting="anthropic_api_key"), settings).allowed


def test_the_mock_is_for_development_only() -> None:
    assert MockJobProvider().validate_source(DEV).allowed
    result = MockJobProvider().validate_source(PROD)
    assert not result.allowed and "development only" in result.reasons[0]


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (401, "", "requires authentication"),
        (407, "", "requires authentication"),
        (403, "", "refused access"),
        (429, "", "rate limiting"),
        (200, "<div class='g-recaptcha'></div>", "CAPTCHA"),
        (200, "Please verify you are human", "CAPTCHA"),
        (503, "cf-challenge running", "CAPTCHA"),
    ],
)
def test_refusals_and_challenges_stop_the_request(status: int, body: str, reason: str) -> None:
    with pytest.raises(AccessDenied, match=reason):
        check_response(status, {"Retry-After": "60"}, body)


def test_ordinary_responses_pass() -> None:
    check_response(200, {}, '{"jobs": []}')


def test_robots_rules_are_honoured() -> None:
    robots = "User-agent: *\nDisallow: /private/\n\nUser-agent: CareerPilotBot\nDisallow: /jobs/"
    assert not robots_allows(robots, "https://example.com/jobs/1")
    assert robots_allows(robots, "https://example.com/feed.json")


# --- Registry --------------------------------------------------------------------------------


def test_unknown_sources_are_listed_but_unavailable() -> None:
    settings = Settings(
        _env_file=None, app_env="development", discovery_providers="mock,linkedin-scraper"
    )
    registry = build_registry(settings)
    statuses = {s.name: s for s in registry.statuses()}
    assert statuses["mock"].enabled
    assert not statuses["linkedin-scraper"].enabled
    assert "No adapter exists" in statuses["linkedin-scraper"].reasons[0]
    assert [p.name for p in registry.enabled()] == ["mock"]


def test_invalid_sources_are_never_enabled() -> None:
    registry = ProviderRegistry([MockJobProvider()], PROD)
    assert registry.enabled() == [] and registry.get("mock") is None
    [status] = registry.statuses()
    assert not status.enabled and status.access_kind == "mock"
