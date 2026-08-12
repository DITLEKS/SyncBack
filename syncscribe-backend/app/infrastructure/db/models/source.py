"""
Путь в репозитории: app/infrastructure/db/models/source.py

Фикс: values_callable у SourceType.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.document_source import document_sources
from app.infrastructure.db.models.enums import SourceType


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[SourceType] = mapped_column(
        sa.Enum(SourceType, name="source_type", values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
    )
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="sources")
    documents: Mapped[list["Document"]] = relationship(secondary=document_sources, back_populates="sources")
