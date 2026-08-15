"""
Путь в репозитории: app/infrastructure/db/models/analysis_job.py

Фикс: values_callable у AnalysisJobStatus.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import AnalysisJobStatus

if TYPE_CHECKING:
    # ИСПРАВЛЕНО (F821): связанные модели импортируются только для статического
    # анализа типов (mypy/ruff), а не во время выполнения — иначе получили бы
    # циклический импорт между document.py, suggestion.py и analysis_job.py. Раньше эти
    # имена использовались только как строковые forward-references в Mapped["..."],
    # которые ruff не мог разрешить и помечал как undefined name.
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.suggestion import Suggestion


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[AnalysisJobStatus] = mapped_column(
        sa.Enum(AnalysisJobStatus, name="analysis_job_status", values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
        default=AnalysisJobStatus.PENDING,
        server_default=AnalysisJobStatus.PENDING.value,
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped["Document"] = relationship(back_populates="analysis_jobs", foreign_keys=[document_id])
    suggestions: Mapped[list["Suggestion"]] = relationship(back_populates="analysis_job", cascade="all, delete-orphan")
