"""Состояние обработки источника внутри задачи анализа (P0-2).

Уникальность (job_id, source_id) защищает от повторной записи правок при
повторной доставке Celery-задачи (acks_late / reject_on_worker_lost).

P0-2b: статус processing + locked_at (lease) защищают от двойного вызова LLM
при конкурентной обработке одного источника.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base

SOURCE_RUN_PENDING = "pending"
SOURCE_RUN_PROCESSING = "processing"
SOURCE_RUN_COMPLETED = "completed"


class AnalysisJobSource(Base):
    __tablename__ = "analysis_job_sources"
    __table_args__ = (
        UniqueConstraint("job_id", "source_id", name="uq_analysis_job_sources_job_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SOURCE_RUN_PENDING, server_default=SOURCE_RUN_PENDING
    )
    suggestions_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
