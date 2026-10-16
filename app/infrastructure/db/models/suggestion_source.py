"""Связь правки со всеми источниками, которые её подтверждают."""

from sqlalchemy import Column, ForeignKey, Table
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.db.base import Base

suggestion_sources = Table(
    "suggestion_sources",
    Base.metadata,
    Column(
        "suggestion_id",
        UUID(as_uuid=True),
        ForeignKey("suggestions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "source_id",
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)
