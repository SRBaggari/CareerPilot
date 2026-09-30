"""Embedding provider abstraction.

Retrieval code depends on ``EmbeddingProvider`` only. Every provider must return vectors of
``EMBEDDING_DIMENSIONS`` (the pgvector column size), and vectors from different models are
never compared: stored embeddings carry the model name that produced them.
"""

import hashlib
import itertools
import math
import re
from typing import Literal, Protocol

import httpx2

from app.core.config import Settings
from app.db.vector import EMBEDDING_DIMENSIONS

InputType = Literal["document", "query"]


class EmbeddingError(Exception):
    """Embeddings could not be produced. The message is safe to show to users."""


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int
    # Cosine-similarity cut-offs for (high, medium) retrieval confidence. They depend on
    # the model's score distribution, so each provider declares its own.
    confidence_thresholds: tuple[float, float]

    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
        """Embed ``texts``; ``input_type`` lets asymmetric models treat queries differently."""
        ...


# --- Offline hashing embedder ----------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#]*")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "using",
        "used",
        "use",
    ]
)


def _tokens(text: str) -> list[str]:
    tokens = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token in _STOPWORDS:
            continue
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]  # crude plural folding: "pipelines" -> "pipeline"
        tokens.append(token)
    return tokens


class HashingEmbeddingProvider:
    """Deterministic feature-hashing embeddings (unigrams + bigrams, signed, L2-normalized).

    Similarity reflects shared words, not meaning. It needs no network or API key, which
    makes it the mock for tests and a usable default for local development.
    """

    name = "hash"
    model = "hash-v1"
    dimensions = EMBEDDING_DIMENSIONS
    confidence_thresholds = (0.45, 0.2)

    def _vector(self, text: str) -> list[float]:
        tokens = _tokens(text)
        features = [(t, 1.0) for t in tokens]
        features += [(f"{a} {b}", 0.5) for a, b in itertools.pairwise(tokens)]
        if not features:
            features = [("\x00empty", 1.0)]  # avoid a zero vector (undefined cosine)
        vector = [0.0] * self.dimensions
        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            vector[value % self.dimensions] += weight if value >> 63 else -weight
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
        return [self._vector(text) for text in texts]


# --- Voyage AI -------------------------------------------------------------------------


class VoyageEmbeddingProvider:
    """Voyage AI embeddings over its REST API (https://docs.voyageai.com/reference/embeddings-api)."""

    name = "voyage"
    dimensions = EMBEDDING_DIMENSIONS
    confidence_thresholds = (0.6, 0.45)
    url = "https://api.voyageai.com/v1/embeddings"
    batch_size = 128

    def __init__(
        self,
        api_key: str,
        model: str = "voyage-3.5",
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._transport = transport

    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
        vectors: list[list[float]] = []
        async with httpx2.AsyncClient(timeout=30.0, transport=self._transport) as client:
            for start in range(0, len(texts), self.batch_size):
                vectors += await self._embed_batch(
                    client, texts[start : start + self.batch_size], input_type
                )
        return vectors

    async def _embed_batch(
        self, client: httpx2.AsyncClient, texts: list[str], input_type: InputType
    ) -> list[list[float]]:
        try:
            response = await client.post(
                self.url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "input": texts,
                    "model": self.model,
                    "input_type": input_type,
                    "output_dimension": self.dimensions,
                },
            )
        except httpx2.HTTPError as exc:
            raise EmbeddingError("Could not reach the embedding service.") from exc
        if response.status_code in (401, 403):
            raise EmbeddingError("The embedding service rejected the API key.")
        if response.status_code == 429:
            raise EmbeddingError("The embedding service is rate limiting requests.")
        if response.status_code >= 400:
            raise EmbeddingError(
                f"The embedding service returned an error ({response.status_code})."
            )
        try:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("The embedding service returned an unexpected response.") from exc
        if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError(
                f"Expected {len(texts)} embeddings of {self.dimensions} dimensions."
            )
        return vectors


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "voyage":
        key = settings.voyage_api_key.get_secret_value() if settings.voyage_api_key else ""
        if not key:
            raise EmbeddingError("Voyage embeddings are configured but VOYAGE_API_KEY is not set.")
        return VoyageEmbeddingProvider(key, settings.embedding_model or "voyage-3.5")
    return HashingEmbeddingProvider()
