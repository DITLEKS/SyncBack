"""Association table between suggestions and all supporting sources."""

from sqlalchemy import Column, ForeignKey, Table

from app.infrastructure.db.base import Base

suggestion_sources = Table(
    "suggestion_sources",
    Base.metadata,
    Column(
        "suggestion_id",
        ForeignKey("suggestions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "source_id",
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)
