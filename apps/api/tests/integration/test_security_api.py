"""Security tests on the real API and database: tenant isolation and input handling."""

from collections.abc import AsyncIterator

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.models import TailoredResume
from app.users.models import User

from .conftest import make_user
from .test_applications_api import APPS, _client
from .test_evidence_search import SpyEmbedder
from .test_matching_api import JOBS, build_job, post
from .test_tailored_resume_api import build_profile

pytestmark = pytest.mark.anyio


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(db, user, embedder) as client:
        yield client


async def test_a_resume_edit_cannot_reveal_another_candidates_evidence(
    api: httpx2.AsyncClient, db: AsyncSession, embedder: SpyEmbedder
) -> None:
    # Another candidate's private evidence.
    victim = await make_user(db, "victim@example.test")
    async with _client(db, victim, embedder) as other:
        victim_ids = await build_profile(other)
    secret_id = victim_ids["rag"]

    # My resume, edited to cite the victim's evidence next to my own.
    await build_profile(api)
    job_id = await build_job(api)
    resume = await post(api, f"{JOBS}/{job_id}/tailored-resumes")
    content = resume["content"]
    claim = (content["summary"] or content["skills"])[0]
    claim["evidence_ids"] = [*claim["evidence_ids"], secret_id]
    edited = await api.put(f"/api/v1/tailored-resumes/{resume['id']}", json={"content": content})
    assert edited.status_code == 200, edited.text

    # The victim's evidence is neither returned nor stored.
    assert secret_id not in edited.text
    fetched = await api.get(f"/api/v1/tailored-resumes/{resume['id']}")
    assert secret_id not in fetched.text
    stored = await db.scalar(select(TailoredResume).where(TailoredResume.id == resume["id"]))
    assert stored is not None and secret_id not in str(stored.content)


async def test_oversized_edits_are_refused(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    resume = await post(api, f"{JOBS}/{job_id}/tailored-resumes")
    content = resume["content"]
    content["summary"] = [{"text": "x" * 3000, "evidence_ids": []}]
    response = await api.put(f"/api/v1/tailored-resumes/{resume['id']}", json={"content": content})
    assert response.status_code == 422
    content["summary"] = [{"text": "Python", "evidence_ids": []}] * 51
    response = await api.put(f"/api/v1/tailored-resumes/{resume['id']}", json={"content": content})
    assert response.status_code == 422


async def test_search_terms_are_data_not_patterns(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    for q in ("abc\\", "%", "_", "' OR 1=1 --", "\\%_"):
        response = await api.get(APPS, params={"q": q})
        assert response.status_code == 200, (q, response.text)
        assert response.json() == []


async def test_unsafe_links_are_refused(api: httpx2.AsyncClient) -> None:
    await build_profile(api)
    job_id = await build_job(api)
    app = await post(api, APPS, {"job_id": job_id})
    for url in ("javascript:alert(document.cookie)", "data:text/html,x", "file:///etc/passwd"):
        response = await api.patch(f"{APPS}/{app['id']}", json={"application_url": url})
        assert response.status_code == 422, url
