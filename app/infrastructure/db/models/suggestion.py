"""
Путь в репозитории: app/infrastructure/db/models/suggestion.py

Фикс: values_callable у ChangeType и SuggestionStatus.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import ChangeType, SuggestionStatus

if TYPE_CHECKING:
    # ИСПРАВЛЕНО (F821): импорт только для статического анализа типов.
    from app.infrastructure.db.models.analysis_job import AnalysisJob

_values = lambda enum_cls: [member.value for member in enum_cls]  # noqa: E731


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    section_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    change_type: Mapped[ChangeType] = mapped_column(sa.Enum(ChangeType, name="change_type", values_callable=_values), nullable=False)
    old_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SuggestionStatus] = mapped_column(
        sa.Enum(SuggestionStatus, name="suggestion_status", values_callable=_values),
        nullable=False,
        default=SuggestionStatus.PENDING,
        server_default=SuggestionStatus.PENDING.value,
    )
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis_job: Mapped["AnalysisJob"] = relationship(back_populates="suggestions")
