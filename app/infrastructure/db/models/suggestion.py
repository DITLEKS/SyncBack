"""
Suggestion ORM model.

C-1 (issue #37): добавлены колонки decided_by (FK -> users.id, nullable)
      и decided_at (datetime, nullable). Эти поля обнуляются при reset_status()
      и заполняются при update_status() / bulk_accept_all / bulk_reject_all.
      reviewed_at оставлен для обратной совместимости (старые миграции).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import ChangeType, SuggestionStatus

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob

_values = lambda e: [m.value for m in e]  # noqa: E731


class Suggestion(Base):
    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_suggestions_confidence_range",
        ),
        # N-5: index=True на analysis_job_id удалён — ix_suggestions_job_status покрывает его.
        Index("ix_suggestions_job_status", "analysis_job_id", "status"),
        # N-6: index=True на document_id удалён — ix_suggestions_document_status покрывает его.
        Index("ix_suggestions_document_status", "document_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        # N-5: index=True удалён — ix_suggestions_job_status (analysis_job_id, status) покрывает всё.
    )
    # R-2: денормализованный document_id — избавляет от JOIN через analysis_jobs.
    # N-6: index=True удалён — ix_suggestions_document_status (document_id, status) покрывает всё.
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    block_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("document_blocks.block_ref", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_type: Mapped[ChangeType] = mapped_column(
        sa.Enum(ChangeType, name="change_type", values_callable=_values),
        nullable=False,
    )
    status: Mapped[SuggestionStatus] = mapped_column(
        sa.Enum(SuggestionStatus, name="suggestion_status", values_callable=_values),
        nullable=False,
        default=SuggestionStatus.PENDING,
        server_default=SuggestionStatus.PENDING.value,
    )
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    # C-1: who made the decision and when
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # reviewed_at kept for backward compat (pre-C-1 data); new code uses decided_at.
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis_job: Mapped["AnalysisJob"] = relationship(back_populates="suggestions")
