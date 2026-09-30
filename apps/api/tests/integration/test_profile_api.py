"""CRUD, validation, ownership, and persistence tests for the profile API."""

from decimal import Decimal
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.models import GeneratedClaim, TailoredResume
from app.jobs.models import Job, JobSource
from app.profiles.models import (
    CandidateEvidence,
    CandidateProfile,
    EvidenceOrigin,
    Project,
    Skill,
)
from app.users.models import User

from .conftest import client_for, make_user

pytestmark = pytest.mark.anyio

BASE = "/api/v1/profile"
PROFILE = {
    "full_name": "  Test Candidate  ",
    "headline": "Aspiring ML engineer",
    "contact_email": "Test.Candidate@Example.TEST",
    "github_url": "github.com/test-candidate",
    "preferred_roles": ["ML Engineer", " ml engineer ", "Data Scientist", ""],
    "preferred_locations": ["Remote", "Hyderabad"],
    "work_modes": ["remote", "hybrid", "remote"],
    "job_types": ["full_time", "internship"],
    "experience_level": "entry_level",
}

# path -> (profile attribute, create payload, replacement payload)
SECTIONS: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {
    "educations": (
        "educations",
        {"institution": "State University", "degree": "B.Sc.", "gpa": "3.6", "gpa_scale": "4",
         "start_date": "2020-09-01", "end_date": "2024-06-01"},
        {"institution": "State University", "degree": "M.Sc."},
    ),
    "work-experiences": (
        "work_experiences",
        {"company_name": "Acme", "title": "Intern", "start_date": "2023-06-01",
         "end_date": "2023-08-31", "employment_type": "internship"},
        {"company_name": "Acme", "title": "Engineer", "is_current": True},
    ),
    "projects": (
        "projects",
        {"title": "Multi-Agent Research Assistant", "repository_url": "github.com/x/y"},
        {"title": "Multi-Agent Research Assistant v2"},
    ),
    "certifications": (
        "certifications",
        {"name": "Cloud Practitioner", "issuer": "Example", "issue_date": "2024-01-10"},
        {"name": "Cloud Architect"},
    ),
    "achievements": (
        "achievements",
        {"title": "Hackathon winner", "achieved_on": "2024-03-01"},
        {"title": "Hackathon finalist"},
    ),
    "coursework": (
        "coursework",
        {"course_name": "Machine Learning", "course_code": "CS229", "grade": "A"},
        {"course_name": "Deep Learning"},
    ),
}  # fmt: skip


async def _create_profile(client: httpx2.AsyncClient) -> dict[str, Any]:
    response = await client.post(BASE, json=PROFILE)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _error_fields(response: httpx2.Response) -> set[str]:
    return {str(e["loc"][-1]) for e in response.json()["detail"]}


# --- Profile ----------------------------------------------------------------------------


async def test_profile_not_found_before_creation(client: httpx2.AsyncClient) -> None:
    response = await client.get(BASE)
    assert response.status_code == 404


async def test_create_profile_normalizes_input(client: httpx2.AsyncClient) -> None:
    body = await _create_profile(client)
    assert body["full_name"] == "Test Candidate"
    assert body["contact_email"] == "test.candidate@example.test"
    assert body["github_url"] == "https://github.com/test-candidate"
    assert body["preferred_roles"] == ["ML Engineer", "Data Scientist"]
    assert body["work_modes"] == ["remote", "hybrid"]
    assert body["job_types"] == ["full_time", "internship"]
    assert body["experience_level"] == "entry_level"
    assert body["pending_suggestions"] == 0
    assert (await client.get(BASE)).json() == body


async def test_create_profile_twice_conflicts(client: httpx2.AsyncClient) -> None:
    await _create_profile(client)
    assert (await client.post(BASE, json=PROFILE)).status_code == 409


async def test_patch_updates_only_given_fields(client: httpx2.AsyncClient) -> None:
    await _create_profile(client)
    response = await client.patch(BASE, json={"headline": "ML engineer", "work_modes": ["onsite"]})
    assert response.status_code == 200
    body = response.json()
    assert body["headline"] == "ML engineer"
    assert body["work_modes"] == ["onsite"]
    assert body["preferred_locations"] == ["Remote", "Hyderabad"]  # untouched


async def test_patch_null_clears_optional_fields_safely(client: httpx2.AsyncClient) -> None:
    await _create_profile(client)
    response = await client.patch(
        BASE, json={"headline": None, "github_url": "", "preferred_roles": None}
    )
    body = response.json()
    assert (body["headline"], body["github_url"], body["preferred_roles"]) == (None, None, [])
    assert body["full_name"] == "Test Candidate"


async def test_required_field_cannot_be_cleared(client: httpx2.AsyncClient) -> None:
    await _create_profile(client)
    for value in (None, "   "):
        response = await client.patch(BASE, json={"full_name": value})
        assert response.status_code == 422
        assert _error_fields(response) == {"full_name"}
    assert (await client.get(BASE)).json()["full_name"] == "Test Candidate"


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"contact_email": "not-an-email"}, "contact_email"),
        ({"phone": "call me maybe"}, "phone"),
        ({"linkedin_url": "ftp://example.test"}, "linkedin_url"),
        ({"work_modes": ["on_the_moon"]}, "0"),
        ({"experience_level": "wizard"}, "experience_level"),
        ({"preferred_roles": ["x" * 201]}, "preferred_roles"),
        ({"preferred_locations": [f"City {i}" for i in range(21)]}, "preferred_locations"),
        ({"headline": "x" * 301}, "headline"),
    ],
)
async def test_profile_validation(
    client: httpx2.AsyncClient, patch: dict[str, Any], field: str
) -> None:
    await _create_profile(client)
    response = await client.patch(BASE, json=patch)
    assert response.status_code == 422, response.text
    assert field in _error_fields(response)


async def test_unknown_and_server_controlled_fields_are_rejected(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post(BASE, json={**PROFILE, "user_id": "x"})
    assert response.status_code == 422
    await _create_profile(client)
    response = await client.patch(BASE, json={"id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 422


async def test_delete_profile_removes_all_profile_data(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await _create_profile(client)
    project = (await client.post(f"{BASE}/projects", json={"title": "P"})).json()
    await client.post(
        f"{BASE}/evidence",
        json={"source_type": "project", "subject_id": project["id"], "content": "Did X"},
    )
    await client.post(f"{BASE}/skills", json={"name": "Python"})

    assert (await client.delete(BASE)).status_code == 204
    assert (await client.get(BASE)).status_code == 404
    for model in (CandidateProfile, Project, CandidateEvidence):
        assert await db.scalar(select(func.count()).select_from(model)) == 0
    assert await db.scalar(select(func.count()).select_from(Skill)) == 1  # shared vocabulary
    assert (await client.delete(BASE)).status_code == 404


async def test_changes_are_persisted_in_postgres(
    client: httpx2.AsyncClient, db: AsyncSession, user: User
) -> None:
    await _create_profile(client)
    await client.patch(BASE, json={"summary": "Builds retrieval systems."})
    db.expunge_all()  # read straight from the database, not the identity map
    row = (
        await db.execute(
            text(
                "SELECT full_name, summary, preferred_roles, work_modes, experience_level "
                "FROM candidate_profiles WHERE user_id = :uid"
            ),
            {"uid": user.id},
        )
    ).one()
    assert tuple(row) == (
        "Test Candidate",
        "Builds retrieval systems.",
        ["ML Engineer", "Data Scientist"],
        ["remote", "hybrid"],
        "entry_level",
    )


# --- Sections ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", SECTIONS)
async def test_section_crud(client: httpx2.AsyncClient, path: str) -> None:
    attr, create, replacement = SECTIONS[path]
    await _create_profile(client)

    created = await client.post(f"{BASE}/{path}", json=create)
    assert created.status_code == 201, created.text
    item = created.json()
    assert [i["id"] for i in (await client.get(BASE)).json()[attr]] == [item["id"]]

    replaced = await client.put(f"{BASE}/{path}/{item['id']}", json=replacement)
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    for key, value in replacement.items():
        assert body[key] == value
    # Full replacement: optional fields omitted from the PUT body are cleared.
    cleared = set(create) - set(replacement)
    assert all(body[key] in (None, False) for key in cleared), body

    assert (await client.delete(f"{BASE}/{path}/{item['id']}")).status_code == 204
    assert (await client.get(BASE)).json()[attr] == []
    assert (await client.delete(f"{BASE}/{path}/{item['id']}")).status_code == 404


async def test_section_requires_profile(client: httpx2.AsyncClient) -> None:
    response = await client.post(f"{BASE}/projects", json={"title": "P"})
    assert response.status_code == 404


async def test_sections_are_ordered(client: httpx2.AsyncClient) -> None:
    await _create_profile(client)
    for title, order in (("Second", 2), ("First", 1), ("Third", 3)):
        await client.post(f"{BASE}/projects", json={"title": title, "sort_order": order})
    titles = [p["title"] for p in (await client.get(BASE)).json()["projects"]]
    assert titles == ["First", "Second", "Third"]


@pytest.mark.parametrize(
    ("path", "payload", "message"),
    [
        ("projects", {"title": " "}, "title"),
        ("projects", {"title": "P", "start_date": "2024-02-01", "end_date": "2024-01-01"},
         "end_date"),
        ("projects", {"title": "P", "project_url": "javascript:alert(1)"}, "URL"),
        ("projects", {"title": "P", "start_date": "1850-01-01"}, "1900"),
        ("educations", {"institution": "U", "gpa": "5", "gpa_scale": "4"}, "gpa"),
        ("educations", {"institution": "U", "gpa": "3.5"}, "gpa_scale"),
        ("educations", {"institution": "U", "gpa": "85", "gpa_scale": "100"}, None),
        ("work-experiences",
         {"company_name": "A", "title": "B", "is_current": True, "end_date": "2024-01-01"},
         "current"),
        ("work-experiences", {"company_name": "A", "title": "B", "employment_type": "slave"},
         "employment_type"),
        ("certifications", {"name": "C", "issue_date": "2024-05-01",
                            "expiration_date": "2024-01-01"}, "expiration_date"),
        ("coursework", {"course_name": "C", "education_id": "00000000-0000-0000-0000-000000000001"},
         "education"),
    ],
)  # fmt: skip
async def test_section_validation(
    client: httpx2.AsyncClient, path: str, payload: dict[str, Any], message: str | None
) -> None:
    await _create_profile(client)
    response = await client.post(f"{BASE}/{path}", json=payload)
    if message is None:
        assert response.status_code == 201, response.text
        return
    assert response.status_code == 422, response.text
    assert message in response.text


async def test_coursework_links_to_own_education(client: httpx2.AsyncClient) -> None:
    await _create_profile(client)
    education = (await client.post(f"{BASE}/educations", json={"institution": "U"})).json()
    response = await client.post(
        f"{BASE}/coursework", json={"course_name": "ML", "education_id": education["id"]}
    )
    assert response.status_code == 201
    assert response.json()["education_id"] == education["id"]


async def test_users_cannot_touch_each_others_items(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await _create_profile(client)
    mine = (await client.post(f"{BASE}/educations", json={"institution": "Mine"})).json()

    other = await make_user(db, "other@example.test")
    async with client_for(db, other) as other_client:
        await _create_profile(other_client)
        assert (await other_client.put(
            f"{BASE}/educations/{mine['id']}", json={"institution": "Hijacked"}
        )).status_code == 404  # fmt: skip
        assert (await other_client.delete(f"{BASE}/educations/{mine['id']}")).status_code == 404
        response = await other_client.post(
            f"{BASE}/coursework", json={"course_name": "C", "education_id": mine["id"]}
        )
        assert response.status_code == 422
        assert (await other_client.get(BASE)).json()["educations"] == []

    assert (await client.get(BASE)).json()["educations"][0]["institution"] == "Mine"


# --- Evidence ---------------------------------------------------------------------------


async def test_evidence_crud_nested_under_its_item(client: httpx2.AsyncClient) -> None:
    await _create_profile(client)
    project = (await client.post(f"{BASE}/projects", json={"title": "Research Assistant"})).json()
    created = await client.post(
        f"{BASE}/evidence",
        json={
            "source_type": "project",
            "subject_id": project["id"],
            "content": "Implemented RAG pipeline using document retrieval and QA.",
        },
    )
    assert created.status_code == 201, created.text
    evidence = created.json()
    assert evidence["origin"] == "user_entered"
    assert evidence["confirmed_at"] is not None
    assert evidence["is_cited"] is False

    profile = (await client.get(BASE)).json()
    assert [e["id"] for e in profile["projects"][0]["evidence"]] == [evidence["id"]]

    general = await client.post(
        f"{BASE}/evidence", json={"source_type": "profile", "content": "Fluent in English."}
    )
    assert general.status_code == 201
    assert [e["content"] for e in (await client.get(BASE)).json()["evidence"]] == [
        "Fluent in English."
    ]

    updated = await client.patch(
        f"{BASE}/evidence/{evidence['id']}", json={"content": "Built a RAG pipeline."}
    )
    assert updated.json()["content"] == "Built a RAG pipeline."
    assert (await client.delete(f"{BASE}/evidence/{evidence['id']}")).status_code == 204
    assert (await client.get(BASE)).json()["projects"][0]["evidence"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"source_type": "project", "content": "Missing subject"},
        {"source_type": "profile", "subject_id": "00000000-0000-0000-0000-000000000001",
         "content": "Unexpected subject"},
        {"source_type": "project", "subject_id": "00000000-0000-0000-0000-000000000001",
         "content": "Unknown project"},
        {"source_type": "profile", "content": "   "},
        {"source_type": "profile", "content": "x", "origin": "resume_extracted"},
        {"source_type": "profile", "content": "x", "confirmed_at": "2024-01-01T00:00:00Z"},
    ],
)  # fmt: skip
async def test_evidence_validation(client: httpx2.AsyncClient, payload: dict[str, Any]) -> None:
    await _create_profile(client)
    response = await client.post(f"{BASE}/evidence", json=payload)
    assert response.status_code == 422, response.text


async def test_evidence_subject_type_must_match(client: httpx2.AsyncClient) -> None:
    await _create_profile(client)
    project = (await client.post(f"{BASE}/projects", json={"title": "P"})).json()
    response = await client.post(
        f"{BASE}/evidence",
        json={"source_type": "education", "subject_id": project["id"], "content": "x"},
    )
    assert response.status_code == 422


async def _cite(db: AsyncSession, profile_id: str, evidence_id: str) -> None:
    job = Job(source=JobSource.MANUAL, title="T", company_name="C", description="D")
    db.add(job)
    await db.flush()
    resume = TailoredResume(candidate_profile_id=profile_id, job_id=job.id, content={})
    db.add(resume)
    await db.flush()
    evidence = await db.get(CandidateEvidence, evidence_id)
    assert evidence is not None
    db.add(GeneratedClaim(tailored_resume_id=resume.id, claim_text="Claim", evidence=[evidence]))
    await db.flush()


async def test_cited_evidence_is_protected(client: httpx2.AsyncClient, db: AsyncSession) -> None:
    profile = await _create_profile(client)
    project = (await client.post(f"{BASE}/projects", json={"title": "P"})).json()
    evidence = (
        await client.post(
            f"{BASE}/evidence",
            json={"source_type": "project", "subject_id": project["id"], "content": "Did X"},
        )
    ).json()
    await _cite(db, profile["id"], evidence["id"])

    assert (await client.get(BASE)).json()["projects"][0]["evidence"][0]["is_cited"] is True
    assert (await client.delete(f"{BASE}/evidence/{evidence['id']}")).status_code == 409
    edit = await client.patch(f"{BASE}/evidence/{evidence['id']}", json={"content": "Changed"})
    assert edit.status_code == 409
    assert (await client.delete(f"{BASE}/projects/{project['id']}")).status_code == 409
    # Deleting the whole profile is still allowed (claims are removed with it).
    assert (await client.delete(BASE)).status_code == 204


async def test_deleting_item_removes_its_uncited_evidence(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await _create_profile(client)
    project = (await client.post(f"{BASE}/projects", json={"title": "P"})).json()
    await client.post(
        f"{BASE}/evidence",
        json={"source_type": "project", "subject_id": project["id"], "content": "Did X"},
    )
    assert (await client.delete(f"{BASE}/projects/{project['id']}")).status_code == 204
    assert await db.scalar(select(func.count()).select_from(CandidateEvidence)) == 0


async def test_evidence_origin_is_always_user_entered(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await _create_profile(client)
    await client.post(f"{BASE}/evidence", json={"source_type": "profile", "content": "Fact"})
    origins = set(await db.scalars(select(CandidateEvidence.origin)))
    assert origins == {EvidenceOrigin.USER_ENTERED}


# --- Skills -----------------------------------------------------------------------------


async def test_skill_crud(client: httpx2.AsyncClient, db: AsyncSession) -> None:
    await _create_profile(client)
    created = await client.post(
        f"{BASE}/skills",
        json={"name": "PyTorch", "category": "framework", "proficiency": "intermediate"},
    )
    assert created.status_code == 201, created.text
    skill = created.json()
    assert (skill["name"], skill["category"]) == ("PyTorch", "framework")

    duplicate = await client.post(f"{BASE}/skills", json={"name": "  pytorch "})
    assert duplicate.status_code == 409

    updated = await client.put(
        f"{BASE}/skills/{skill['id']}", json={"proficiency": "advanced", "years_experience": "2.5"}
    )
    assert updated.json()["proficiency"] == "advanced"
    assert Decimal(updated.json()["years_experience"]) == Decimal("2.5")

    assert (await client.delete(f"{BASE}/skills/{skill['id']}")).status_code == 204
    assert (await client.get(BASE)).json()["skills"] == []
    assert await db.scalar(select(func.count()).select_from(Skill)) == 1


async def test_skill_vocabulary_is_shared_between_users(
    client: httpx2.AsyncClient, db: AsyncSession
) -> None:
    await _create_profile(client)
    mine = (await client.post(f"{BASE}/skills", json={"name": "Python"})).json()
    other = await make_user(db, "other@example.test")
    async with client_for(db, other) as other_client:
        await _create_profile(other_client)
        theirs = (await other_client.post(f"{BASE}/skills", json={"name": "PYTHON"})).json()
    assert theirs["skill_id"] == mine["skill_id"]
    assert theirs["name"] == "Python"  # the shared entry is never renamed


@pytest.mark.parametrize(
    "payload",
    [{"name": ""}, {"name": "Go", "years_experience": "61"}, {"name": "Go", "proficiency": "god"}],
)
async def test_skill_validation(client: httpx2.AsyncClient, payload: dict[str, Any]) -> None:
    await _create_profile(client)
    assert (await client.post(f"{BASE}/skills", json=payload)).status_code == 422
