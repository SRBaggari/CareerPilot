"""End-to-end evidence retrieval: indexing, top-k semantic search, filters, verification."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingError, HashingEmbeddingProvider, InputType
from app.api.routes.candidate_evidence import get_embedder
from app.core.errors import ServiceUnavailableError
from app.profiles.models import CandidateEvidence, EvidenceOrigin, EvidenceSourceType
from app.retrieval.service import retrieve_verified_evidence
from app.users.models import User

from .conftest import client_for, make_user

pytestmark = pytest.mark.anyio

SEARCH = "/api/candidate/evidence/search"
INDEX = "/api/candidate/evidence/index"
PROFILE = "/api/v1/profile"


class SpyEmbedder(HashingEmbeddingProvider):
    """The mock embedder, counting how many texts it embeds."""

    def __init__(self) -> None:
        self.documents = 0
        self.queries = 0

    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
        if input_type == "document":
            self.documents += len(texts)
        else:
            self.queries += len(texts)
        return await super().embed(texts, input_type=input_type)


@pytest.fixture
def embedder() -> SpyEmbedder:
    return SpyEmbedder()


@pytest.fixture
async def api(
    db: AsyncSession, user: User, embedder: SpyEmbedder
) -> AsyncIterator[httpx2.AsyncClient]:
    async with client_for(db, user, overrides={get_embedder: lambda: embedder}) as client:
        yield client


async def _post(api: httpx2.AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await api.post(path, json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()  # type: ignore[no-any-return]


async def _evidence(api: httpx2.AsyncClient, type_: str, subject: str | None, content: str) -> str:
    body = {"source_type": type_, "subject_id": subject, "content": content}
    return (await _post(api, f"{PROFILE}/evidence", body))["id"]  # type: ignore[no-any-return]


async def seed(api: httpx2.AsyncClient) -> dict[str, str]:
    """A small, synthetic candidate profile with evidence under several items."""
    profile = await _post(api, PROFILE, {"full_name": "Test Candidate"})
    project = await _post(api, f"{PROFILE}/projects", {"title": "Multi-Agent Research Assistant"})
    job = await _post(
        api,
        f"{PROFILE}/work-experiences",
        {"title": "Machine Learning Intern", "company_name": "Acme Analytics"},
    )
    ids = {
        "candidate": profile["id"],
        "project": project["id"],
        "job": job["id"],
        "rag": await _evidence(
            api,
            "project",
            project["id"],
            "Implemented RAG pipeline using document retrieval and question answering.",
        ),
        "eval": await _evidence(
            api, "project", project["id"], "Evaluated answer faithfulness on 200 questions."
        ),
        "docker": await _evidence(
            api,
            "work_experience",
            job["id"],
            "Deployed the classification model with FastAPI and Docker on AWS.",
        ),
        "mentor": await _evidence(
            api, "profile", None, "Mentored 60 students in data structures lab sessions."
        ),
    }
    return ids


async def _search(
    api: httpx2.AsyncClient, candidate: str, query: str, **extra: Any
) -> dict[str, Any]:
    return await _post(api, SEARCH, {"candidate_id": candidate, "query": query, **extra})


# --- Retrieval --------------------------------------------------------------------------


async def test_search_returns_ranked_evidence_with_sources(api: httpx2.AsyncClient) -> None:
    ids = await seed(api)
    body = await _search(
        api, ids["candidate"], "RAG pipeline for question answering over documents", top_k=3
    )

    assert body["embedding_model"] == "hash-v1"
    assert body["newly_indexed"] == 4  # indexed on demand
    results = body["results"]
    assert len(results) == 3
    top = results[0]
    assert top["evidence_id"] == ids["rag"]
    assert top["factual_content"].startswith("Implemented RAG pipeline")
    assert top["verification_status"] == "verified"
    assert top["source"] == {
        "record_type": "project",
        "record_id": ids["project"],
        "record_label": "Multi-Agent Research Assistant",
        "origin": "user_entered",
        "resume_id": None,
        "resume_file_name": None,
    }
    similarities = [r["similarity"] for r in results]
    assert similarities == sorted(similarities, reverse=True)
    assert body["retrieval_confidence"] == top["confidence"]
    assert {r["candidate_id"] for r in results} == {ids["candidate"]}


async def test_item_context_is_searchable(api: httpx2.AsyncClient) -> None:
    ids = await seed(api)
    # "Acme" and "intern" only appear in the job the evidence belongs to, not in the claim.
    body = await _search(api, ids["candidate"], "Acme Analytics machine learning intern", top_k=1)
    [top] = body["results"]
    assert top["evidence_id"] == ids["docker"]
    assert top["source"]["record_label"] == "Machine Learning Intern at Acme Analytics"


async def test_top_k_limits_results(api: httpx2.AsyncClient) -> None:
    ids = await seed(api)
    assert len((await _search(api, ids["candidate"], "students", top_k=1))["results"]) == 1
    assert len((await _search(api, ids["candidate"], "students", top_k=50))["results"]) == 4


async def test_filter_by_evidence_type(api: httpx2.AsyncClient) -> None:
    ids = await seed(api)
    body = await _search(
        api, ids["candidate"], "model deployment", evidence_type="project", top_k=10
    )
    assert {r["evidence_type"] for r in body["results"]} == {"project"}
    assert len(body["results"]) == 2


async def test_min_similarity_filters_weak_matches(api: httpx2.AsyncClient) -> None:
    ids = await seed(api)
    body = await _search(
        api, ids["candidate"], "RAG pipeline question answering", top_k=10, min_similarity=0.2
    )
    assert body["results"] and all(r["similarity"] >= 0.2 for r in body["results"])
    assert len(body["results"]) < 4


async def test_no_evidence_means_no_confidence(api: httpx2.AsyncClient) -> None:
    profile = await _post(api, PROFILE, {"full_name": "Empty Candidate"})
    body = await _search(api, profile["id"], "anything")
    assert (body["results"], body["retrieval_confidence"]) == ([], "none")


# --- Verification -----------------------------------------------------------------------


async def _add_unverified(db: AsyncSession, candidate_id: str, content: str) -> CandidateEvidence:
    """Simulates evidence awaiting confirmation (no API path creates this today)."""
    evidence = CandidateEvidence(
        candidate_profile_id=candidate_id,
        source_type=EvidenceSourceType.PROFILE,
        origin=EvidenceOrigin.RESUME_EXTRACTED,
        content=content,
        confirmed_at=None,
    )
    db.add(evidence)
    await db.flush()
    return evidence


async def test_unverified_evidence_is_excluded_by_default(
    api: httpx2.AsyncClient, db: AsyncSession, embedder: SpyEmbedder
) -> None:
    ids = await seed(api)
    unverified = await _add_unverified(db, ids["candidate"], "Led the RAG pipeline research team.")
    query = "RAG pipeline research team"

    default = await _search(api, ids["candidate"], query, top_k=10)
    assert str(unverified.id) not in {r["evidence_id"] for r in default["results"]}

    shown = await _search(api, ids["candidate"], query, top_k=10, include_unverified=True)
    flagged = [r for r in shown["results"] if r["evidence_id"] == str(unverified.id)]
    assert [r["verification_status"] for r in flagged] == ["unverified"]

    # The generation entry point has no way to include it.
    retrieved = await retrieve_verified_evidence(
        db, unverified.candidate_profile_id, query, embedder, top_k=10
    )
    assert retrieved and all(r.verification_status == "verified" for r in retrieved)
    assert unverified.id not in {r.evidence_id for r in retrieved}


async def test_verification_status_is_derived_by_the_database(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    ids = await seed(api)
    unverified = await _add_unverified(db, ids["candidate"], "Pending claim")
    result = await db.execute(text("SELECT id, verification_status FROM candidate_evidence"))
    rows: dict[uuid.UUID, str] = {row_id: status for row_id, status in result}
    assert rows[unverified.id] == "unverified"
    assert {v for k, v in rows.items() if k != unverified.id} == {"verified"}


# --- Indexing ---------------------------------------------------------------------------


async def test_indexing_is_incremental_and_follows_edits(
    api: httpx2.AsyncClient, db: AsyncSession, embedder: SpyEmbedder
) -> None:
    ids = await seed(api)
    await _search(api, ids["candidate"], "anything")
    assert embedder.documents == 4
    second = await _search(api, ids["candidate"], "anything")
    assert (second["newly_indexed"], embedder.documents) == (0, 4)  # nothing re-embedded

    edited = await api.patch(
        f"{PROFILE}/evidence/{ids['mentor']}",
        json={"content": "Taught Kubernetes workshops to 60 students."},
    )
    assert edited.status_code == 200
    row = await db.get(CandidateEvidence, ids["mentor"])
    assert row is not None and row.embedding is None  # stale vector cleared

    body = await _search(api, ids["candidate"], "Kubernetes workshops", top_k=1)
    assert body["newly_indexed"] == 1 and embedder.documents == 5
    assert body["results"][0]["evidence_id"] == ids["mentor"]
    await db.refresh(row)
    assert row.embedding_model == "hash-v1" and row.embedded_at is not None


async def test_vectors_from_another_model_are_reembedded_not_mixed(
    api: httpx2.AsyncClient, db: AsyncSession, embedder: SpyEmbedder
) -> None:
    ids = await seed(api)
    await _search(api, ids["candidate"], "anything")
    await db.execute(text("UPDATE candidate_evidence SET embedding_model = 'other-model'"))
    body = await _search(api, ids["candidate"], "RAG pipeline")
    assert body["newly_indexed"] == 4
    assert set(await db.scalars(select(CandidateEvidence.embedding_model))) == {"hash-v1"}


async def test_index_endpoint(api: httpx2.AsyncClient, embedder: SpyEmbedder) -> None:
    ids = await seed(api)
    first = await _post(api, INDEX, {"candidate_id": ids["candidate"]})
    assert (first["indexed"], first["total"], first["embedding_model"]) == (4, 4, "hash-v1")
    assert (await _post(api, INDEX, {"candidate_id": ids["candidate"]}))["indexed"] == 0
    assert (await _post(api, INDEX, {"candidate_id": ids["candidate"], "force": True}))[
        "indexed"
    ] == 4
    assert embedder.documents == 8


# --- Isolation, validation, failures ----------------------------------------------------


async def test_filtered_search_still_returns_top_k_among_many_rows(
    api: httpx2.AsyncClient, db: AsyncSession, user: User, embedder: SpyEmbedder
) -> None:
    """Another candidate's many similar rows must neither leak nor crowd out results (the
    HNSW-then-filter pitfall)."""
    ids = await seed(api)
    other = await make_user(db, "other@example.test")
    async with client_for(db, other, overrides={get_embedder: lambda: embedder}) as other_api:
        other_profile = await _post(other_api, PROFILE, {"full_name": "Other Candidate"})
        for i in range(60):
            await _evidence(other_api, "profile", None, f"Implemented RAG pipeline variant {i}.")
        await _post(other_api, INDEX, {"candidate_id": other_profile["id"]})

    body = await _search(api, ids["candidate"], "Implemented RAG pipeline", top_k=4)
    assert len(body["results"]) == 4
    assert {r["candidate_id"] for r in body["results"]} == {ids["candidate"]}


async def test_candidates_are_private(
    api: httpx2.AsyncClient, db: AsyncSession, embedder: SpyEmbedder
) -> None:
    ids = await seed(api)
    other = await make_user(db, "other@example.test")
    async with client_for(db, other, overrides={get_embedder: lambda: embedder}) as other_api:
        await _post(other_api, PROFILE, {"full_name": "Other Candidate"})
        for path in (SEARCH, INDEX):
            response = await other_api.post(
                path,
                json={"candidate_id": ids["candidate"], "query": "x"}
                if path == SEARCH
                else {"candidate_id": ids["candidate"]},
            )
            assert response.status_code == 404
    unknown = await api.post(
        SEARCH, json={"candidate_id": "00000000-0000-0000-0000-000000000001", "query": "x"}
    )
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": 51},
        {"query": "x", "evidence_type": "hobby"},
        {"query": "x", "verified_only": False},
        {"query": "x", "min_similarity": 2},
    ],
)
async def test_search_validation(api: httpx2.AsyncClient, body: dict[str, Any]) -> None:
    ids = await seed(api)
    response = await api.post(SEARCH, json={"candidate_id": ids["candidate"], **body})
    assert response.status_code == 422


async def test_embedding_outage_returns_503(db: AsyncSession, user: User) -> None:
    class Down(HashingEmbeddingProvider):
        async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
            raise EmbeddingError("Could not reach the embedding service.")

    async with client_for(db, user, overrides={get_embedder: Down}) as api:
        ids = await seed(api)
        response = await api.post(SEARCH, json={"candidate_id": ids["candidate"], "query": "x"})
    assert response.status_code == 503
    assert "Could not reach the embedding service" in response.text


async def test_misconfigured_provider_returns_503(db: AsyncSession, user: User) -> None:
    def misconfigured() -> HashingEmbeddingProvider:
        raise ServiceUnavailableError("Evidence search is unavailable: VOYAGE_API_KEY is not set.")

    async with client_for(db, user, overrides={get_embedder: misconfigured}) as api:
        ids = await seed(api)
        response = await api.post(SEARCH, json={"candidate_id": ids["candidate"], "query": "x"})
    assert response.status_code == 503


async def test_resume_evidence_references_its_upload(
    api: httpx2.AsyncClient, db: AsyncSession
) -> None:
    ids = await seed(api)
    await db.execute(
        text(
            "INSERT INTO resumes (id, candidate_profile_id, file_name, file_format, storage_key, "
            "file_size_bytes, sha256) VALUES ('00000000-0000-0000-0000-0000000000aa', :c, "
            "'priya.pdf', 'pdf', 'k', 1, :sha)"
        ),
        {"c": ids["candidate"], "sha": "a" * 64},
    )
    await db.execute(
        text(
            "UPDATE candidate_evidence SET origin = 'resume_extracted', "
            "source_resume_id = '00000000-0000-0000-0000-0000000000aa' WHERE id = :id"
        ),
        {"id": ids["rag"]},
    )
    body = await _search(api, ids["candidate"], "RAG pipeline", top_k=1)
    source = body["results"][0]["source"]
    assert source["origin"] == "resume_extracted"
    assert source["resume_file_name"] == "priya.pdf"
