"""pgvector helpers.

The embedding dimension is part of the database schema, not runtime configuration: changing
it requires a migration and re-embedding every stored vector. The configured embedding
provider must produce vectors of exactly this size.
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

EMBEDDING_DIMENSIONS = 1024


class EmbeddingMixin:
    """Nullable embedding plus the model that produced it (vectors from different models
    are not comparable)."""

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(100))


def hnsw_cosine_index(table_name: str, column: str = "embedding") -> Index:
    """Approximate-nearest-neighbour index for cosine distance (``<=>``)."""
    return Index(
        f"ix_{table_name}_{column}_hnsw",
        column,
        postgresql_using="hnsw",
        postgresql_ops={column: "vector_cosine_ops"},
    )
