"""
Связующая таблица M:N между документами и источниками.
PK — составной (document_id, source_id), суррогатный id не нужен.
"""

from sqlalchemy import Column, ForeignKey, Table
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.db.base import Base

document_sources = Table(
    "document_sources",
    Base.metadata,
    Column(
        "document_id",
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "source_id",
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
)
