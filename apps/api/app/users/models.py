from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.profiles.models import CandidateProfile


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An account. Authentication (Auth.js) tables are added in the auth phase."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    # Stored lowercased so the unique constraint is case-insensitive.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    image_url: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[CandidateProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
