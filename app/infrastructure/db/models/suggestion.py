"""Модель Suggestion."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import SuggestionStatus

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.source import Source
    from app.infrastructure.db.models.user import User


class Suggestion(Base):
    """Правка документа."""

    __tablename__ = "suggestions"
    __table_args__ = (
        UniqueConstraint("document_id", "analysis_job_id", "seq", name="uq_suggestions_doc_job_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    analysis_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    section_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SuggestionStatus] = mapped_column(nullable=False, default=SuggestionStatus.PENDING, index=True)
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    block_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("document_blocks.id", ondelete="SET NULL"), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    document: Mapped[Document] = relationship(back_populates="suggestions")
    analysis_job: Mapped[AnalysisJob] = relationship(back_populates="suggestions")
    decided_by_user: Mapped[User | None] = relationship(foreign_keys=[decided_by])
    sources: Mapped[list[Source]] = relationship(secondary="suggestion_sources", lazy="selectin")
