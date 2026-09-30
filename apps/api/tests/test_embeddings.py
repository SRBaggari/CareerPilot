"""Embedding providers and retrieval helpers (no database, no network)."""

import json
import math
from typing import Any

import httpx2
import pytest

from app.ai.embeddings import (
    EmbeddingError,
    HashingEmbeddingProvider,
    VoyageEmbeddingProvider,
    get_embedding_provider,
)
from app.core.config import Settings
from app.db.vector import EMBEDDING_DIMENSIONS
from app.profiles.models import (
    CandidateEvidence,
    EvidenceOrigin,
    EvidenceSourceType,
    Project,
    WorkExperience,
)
from app.retrieval.service import chunk_text, confidence_for, record_label

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- Hashing (mock) embedder ------------------------------------------------------------


async def test_hash_embeddings_are_deterministic_normalized_and_sized() -> None:
    provider = HashingEmbeddingProvider()
    [a, b] = await provider.embed(
        ["Built a RAG pipeline", "Built a RAG pipeline"], input_type="document"
    )
    assert a == b
    assert len(a) == EMBEDDING_DIMENSIONS
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)


async def test_hash_embeddings_reflect_word_overlap() -> None:
    provider = HashingEmbeddingProvider()
    [query] = await provider.embed(["retrieval augmented generation pipeline"], input_type="query")
    related, unrelated = await provider.embed(
        [
            "Implemented retrieval pipelines for question answering",
            "Mentored students in a data structures lab",
        ],
        input_type="document",
    )
    assert cosine(query, related) > cosine(query, unrelated)


async def test_hash_embeddings_never_produce_a_zero_vector() -> None:
    [vector] = await HashingEmbeddingProvider().embed(["!!! ---"], input_type="document")
    assert any(vector)


# --- Voyage adapter ---------------------------------------------------------------------


def voyage_transport(
    requests: list[dict[str, Any]], *, status: int = 200, dims: int = EMBEDDING_DIMENSIONS
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append({"headers": dict(request.headers), "body": body, "url": str(request.url)})
        if status != 200:
            return httpx2.Response(status, json={"detail": "nope"})
        data = [{"index": i, "embedding": [float(i)] * dims} for i in range(len(body["input"]))]
        return httpx2.Response(
            200, json={"data": list(reversed(data)), "usage": {"total_tokens": 3}}
        )

    return httpx2.MockTransport(handler)


async def test_voyage_request_shape_and_ordering() -> None:
    requests: list[dict[str, Any]] = []
    provider = VoyageEmbeddingProvider("k-123", transport=voyage_transport(requests))
    vectors = await provider.embed(["a", "b", "c"], input_type="query")
    [request] = requests
    assert request["url"] == "https://api.voyageai.com/v1/embeddings"
    assert request["headers"]["authorization"] == "Bearer k-123"
    assert request["body"] == {
        "input": ["a", "b", "c"],
        "model": "voyage-3.5",
        "input_type": "query",
        "output_dimension": EMBEDDING_DIMENSIONS,
    }
    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]  # re-ordered by index


async def test_voyage_batches_large_inputs() -> None:
    requests: list[dict[str, Any]] = []
    provider = VoyageEmbeddingProvider("k", transport=voyage_transport(requests))
    vectors = await provider.embed([f"t{i}" for i in range(130)], input_type="document")
    assert [len(r["body"]["input"]) for r in requests] == [128, 2]
    assert len(vectors) == 130


@pytest.mark.parametrize(
    ("status", "dims", "message"),
    [
        (401, EMBEDDING_DIMENSIONS, "rejected the API key"),
        (429, EMBEDDING_DIMENSIONS, "rate limiting"),
        (500, EMBEDDING_DIMENSIONS, r"error \(500\)"),
        (200, 512, "1024 dimensions"),
    ],
)
async def test_voyage_errors_are_explained(status: int, dims: int, message: str) -> None:
    provider = VoyageEmbeddingProvider(
        "k", transport=voyage_transport([], status=status, dims=dims)
    )
    with pytest.raises(EmbeddingError, match=message):
        await provider.embed(["x"], input_type="document")


async def test_voyage_network_failure() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    provider = VoyageEmbeddingProvider("k", transport=httpx2.MockTransport(fail))
    with pytest.raises(EmbeddingError, match="Could not reach"):
        await provider.embed(["x"], input_type="document")


def test_provider_selection() -> None:
    def settings(**overrides: Any) -> Settings:
        return Settings(_env_file=None, **overrides)

    assert isinstance(get_embedding_provider(settings()), HashingEmbeddingProvider)
    with pytest.raises(EmbeddingError, match="VOYAGE_API_KEY"):
        get_embedding_provider(settings(embedding_provider="voyage"))
    voyage = get_embedding_provider(
        settings(embedding_provider="voyage", voyage_api_key="k", embedding_model="voyage-3-large")
    )
    assert (voyage.name, voyage.model) == ("voyage", "voyage-3-large")


# --- Chunks and confidence --------------------------------------------------------------


def test_chunk_text_adds_the_item_context() -> None:
    evidence = CandidateEvidence(
        source_type=EvidenceSourceType.WORK_EXPERIENCE,
        origin=EvidenceOrigin.USER_ENTERED,
        content="Reduced tagging effort by 40%.",
        work_experience=WorkExperience(title="ML Intern", company_name="Acme"),
    )
    assert record_label(evidence) == "ML Intern at Acme"
    assert (
        chunk_text(evidence) == "Work experience: ML Intern at Acme\nReduced tagging effort by 40%."
    )
    project = CandidateEvidence(
        source_type=EvidenceSourceType.PROJECT,
        origin=EvidenceOrigin.USER_ENTERED,
        content="Built X.",
        project=Project(title="MARA"),
    )
    assert chunk_text(project).startswith("Project: MARA\n")
    general = CandidateEvidence(
        source_type=EvidenceSourceType.PROFILE,
        origin=EvidenceOrigin.USER_ENTERED,
        content="Fluent.",
    )
    assert chunk_text(general) == "Fluent."


@pytest.mark.parametrize(
    ("similarity", "expected"), [(0.9, "high"), (0.6, "high"), (0.5, "medium"), (0.1, "low")]
)
def test_confidence_labels(similarity: float, expected: str) -> None:
    assert confidence_for(similarity, (0.6, 0.45)) == expected
