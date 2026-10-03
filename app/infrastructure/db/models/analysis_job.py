"""
Путь в репозитории: app/infrastructure/db/models/analysis_job.py

Фикс: values_callable у AnalysisJobStatus.
P0-7: добавлена колонка idempotency_key (nullable, unique per document).
R-6: удалён одиночный index=True на idempotency_key — все запросы
    идут через get_by_idempotency_key(document_id, key), поэтому
    составной уникальный индекс uq_analysis_jobs_doc_idem_key
    (document_id, idempotency_key) полностью покрывает этот кейс.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import AnalysisJobStatus

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.suggestion import Suggestion


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        # Один ключ идемпотентности может относиться только к одному документу.
        # NULL-значения не нарушают уникальность в PostgreSQL.
        # R-6: этот составной уникальный индекс покрывает запросы
        #     WHERE document_id = ? AND idempotency_key = ?, поэтому
        #     одиночный B-tree на idempotency_key был лишним.
        UniqueConstraint("document_id", "idempotency_key", name="uq_analysis_jobs_doc_idem_key"),
        # У документа не может быть двух незавершённых задач одновременно.
        Index(
            "uq_analysis_jobs_one_active_per_document",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'dispatched', 'processing')"),
            sqlite_where=text("status IN ('pending', 'dispatched', 'processing')"),
        ),
        # Последняя задача документа: WHERE document_id = ? ORDER BY created_at DESC.
        Index("ix_analysis_jobs_doc_latest", "document_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
    # R-6: index=True удалён — uq_analysis_jobs_doc_idem_key полностью покрывает этот кейс.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    partial_success: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped["Document"] = relationship(
        back_populates="analysis_jobs", foreign_keys=[document_id]
    )
    suggestions: Mapped[list["Suggestion"]] = relationship(
        back_populates="analysis_job", cascade="all, delete-orphan"
    )
