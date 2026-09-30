"""AI-generated content never modifies the profile without explicit acceptance."""

from typing import Any

import httpx2
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldValidationError
from app.profiles.models import (
    CandidateProfile,
    SuggestionAction,
    SuggestionSection,
    SuggestionSource,
)
from app.profiles.suggestions import create_suggestion

pytestmark = pytest.mark.anyio

BASE = "/api/v1/profile"


async def _setup(client: httpx2.AsyncClient, db: AsyncSession) -> CandidateProfile:
    response = await client.post(BASE, json={"full_name": "Test Candidate"})
    assert response.status_code == 201
    profile = await db.get(CandidateProfile, response.json()["id"])
    assert profile is not None
    return profile


async def _suggest(
    db: AsyncSession, profile: CandidateProfile, section: SuggestionSection, data: dict[str, Any],
    *, action: SuggestionAction = SuggestionAction.CREATE, target_id: Any = None,
    source: SuggestionSource = SuggestionSource.AI_GENERATION,
) -> str:  # fmt: skip
    suggestion = await create_suggestion(
        db, profile, section=section, action=action, proposed_data=data, source=source,
        rationale="Found in uploaded resume", target_id=target_id,
    )  # fmt: skip
    return str(suggestion.id)


async def test_suggestion_does_not_modify_profile_until_accepted(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    profile = await _setup(client, db)
    suggestion_id = await _suggest(
        db, profile, SuggestionSection.PROJECT, {"title": "Multi-Agent Research Assistant"}
    )

    body = (await client.get(BASE)).json()
    assert body["projects"] == []
    assert body["pending_suggestions"] == 1
    pending = (await client.get(f"{BASE}/suggestions")).json()
    assert [s["id"] for s in pending] == [suggestion_id]
    assert pending[0]["proposed_data"] == {"title": "Multi-Agent Research Assistant"}

    accepted = await client.post(f"{BASE}/suggestions/{suggestion_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["reviewed_at"] is not None

    body = (await client.get(BASE)).json()
    assert [p["title"] for p in body["projects"]] == ["Multi-Agent Research Assistant"]
    assert body["projects"][0]["id"] == accepted.json()["applied_target_id"]
    assert body["pending_suggestions"] == 0


async def test_rejected_suggestion_leaves_profile_unchanged(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    profile = await _setup(client, db)
    suggestion_id = await _suggest(
        db, profile, SuggestionSection.PERSONAL_INFO, {"headline": "AI-written headline"},
        action=SuggestionAction.UPDATE,
    )  # fmt: skip
    rejected = await client.post(f"{BASE}/suggestions/{suggestion_id}/reject")
    assert rejected.json()["status"] == "rejected"
    assert (await client.get(BASE)).json()["headline"] is None
    assert (await client.post(f"{BASE}/suggestions/{suggestion_id}/accept")).status_code == 409
    assert (await client.get(f"{BASE}/suggestions")).json() == []
    history = (await client.get(f"{BASE}/suggestions", params={"status_filter": "rejected"})).json()
    assert [s["id"] for s in history] == [suggestion_id]


async def test_accepted_extracted_evidence_records_its_origin(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    profile = await _setup(client, db)
    suggestion_id = await _suggest(
        db, profile, SuggestionSection.EVIDENCE,
        {"source_type": "profile", "content": "Led a team of four students."},
        source=SuggestionSource.RESUME_EXTRACTION,
    )  # fmt: skip
    await client.post(f"{BASE}/suggestions/{suggestion_id}/accept")
    [evidence] = (await client.get(BASE)).json()["evidence"]
    assert evidence["origin"] == "resume_extracted"
    assert evidence["confirmed_at"] is not None


async def test_update_suggestion_is_validated_as_a_whole_and_atomic(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    profile = await _setup(client, db)
    project = (
        await client.post(f"{BASE}/projects", json={"title": "P", "start_date": "2024-03-01"})
    ).json()
    suggestion_id = await _suggest(
        db, profile, SuggestionSection.PROJECT, {"end_date": "2024-01-01"},
        action=SuggestionAction.UPDATE, target_id=project["id"],
    )  # fmt: skip
    response = await client.post(f"{BASE}/suggestions/{suggestion_id}/accept")
    assert response.status_code == 422
    # Neither the profile nor the suggestion changed.
    assert (await client.get(BASE)).json()["projects"][0]["end_date"] is None
    [pending] = (await client.get(f"{BASE}/suggestions")).json()
    assert pending["status"] == "pending"


async def test_update_suggestion_merges_into_existing_item(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    profile = await _setup(client, db)
    project = (await client.post(f"{BASE}/projects", json={"title": "P", "role": "Lead"})).json()
    suggestion_id = await _suggest(
        db, profile, SuggestionSection.PROJECT, {"description": "A research assistant."},
        action=SuggestionAction.UPDATE, target_id=project["id"],
    )  # fmt: skip
    assert (await client.post(f"{BASE}/suggestions/{suggestion_id}/accept")).status_code == 200
    [updated] = (await client.get(BASE)).json()["projects"]
    assert (updated["title"], updated["role"], updated["description"]) == (
        "P",
        "Lead",
        "A research assistant.",
    )


@pytest.mark.parametrize(
    ("section", "action", "data"),
    [
        (SuggestionSection.PROJECT, SuggestionAction.CREATE, {"title": ""}),
        (SuggestionSection.PROJECT, SuggestionAction.UPDATE, {"not_a_field": 1}),
        (SuggestionSection.SKILL, SuggestionAction.CREATE, {"name": "Go", "level": 9}),
        (SuggestionSection.PERSONAL_INFO, SuggestionAction.CREATE, {"headline": "x"}),
        (SuggestionSection.EVIDENCE, SuggestionAction.CREATE, {"source_type": "profile"}),
    ],
)
async def test_malformed_ai_output_is_rejected_before_storage(
    client: httpx2.AsyncClient,
    db: AsyncSession,
    section: SuggestionSection,
    action: SuggestionAction,
    data: dict[str, Any],
) -> None:
    profile = await _setup(client, db)
    with pytest.raises((ValidationError, FieldValidationError)):
        await _suggest(db, profile, section, data, action=action, target_id=profile.id)
