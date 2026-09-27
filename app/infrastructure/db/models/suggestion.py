import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
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
        # Уверенность модели: от 0.000 до 1.000.
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_suggestions_confidence_range",
        ),
        # Составной индекс для основного запроса: pending suggestions по job.
        Index("ix_suggestions_job_status", "analysis_job_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    # Якорная привязка к конкретному блоку документа.
    # FK SET NULL: удаление блока не должно удалять правку.
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
    old_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SuggestionStatus] = mapped_column(
        sa.Enum(SuggestionStatus, name="suggestion_status", values_callable=_values),
        nullable=False,
        default=SuggestionStatus.PENDING,
        server_default=SuggestionStatus.PENDING.value,
    )
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # NUMERIC(4,3): точное хранение 0.000–1.000, без IEEE 754 погрешности.
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis_job: Mapped["AnalysisJob"] = relationship(back_populates="suggestions")
