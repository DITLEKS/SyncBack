"""
Путь в репозитории: app/infrastructure/db/models/analysis_job.py

Фикс: values_callable у AnalysisJobStatus.
P0-7: добавлена колонка idempotency_key (nullable, unique per document).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import AnalysisJobStatus

if TYPE_CHECKING:
    # ИСПРАВЛЕНО (F821): связанные модели импортируются только для статического
    # анализа типов (mypy/ruff), а не во время выполнения — иначе получили бы
    # циклический импорт между document.py, suggestion.py и analysis_job.py.
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.suggestion import Suggestion


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        # Один ключ идемпотентности может относиться только к одному документу.
        # NULL-значения не нарушают уникальность в PostgreSQL, поэтому строки
        # без idempotency_key могут сосуществовать без ограничений.
        UniqueConstraint("document_id", "idempotency_key", name="uq_analysis_jobs_doc_idem_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[AnalysisJobStatus] = mapped_column(
        sa.Enum(
            AnalysisJobStatus,
            name="analysis_job_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=AnalysisJobStatus.PENDING,
        server_default=AnalysisJobStatus.PENDING.value,
    )
    # P0-7: ключ идемпотентности, переданный клиентом при создании job.
    # Nullable — старые записи и запросы без заголовка не имеют ключа.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # P0-7: флаг частичного успеха — часть суждений создана, но были ошибки.
    partial_success: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped["Document"] = relationship(back_populates="analysis_jobs", foreign_keys=[document_id])
    suggestions: Mapped[list["Suggestion"]] = relationship(back_populates="analysis_job", cascade="all, delete-orphan")
