"""Репозиторий задач анализа.

ИСПРАВЛЕНО (code-review):
- C-5: mark_dispatched — refresh перемещён после записи изменений
- Q-4: логика started_at/finished_at удалена из update_status —
        теперь это ответственность AnalysisJobService.update_job_status
- A-1: класс реализует IAnalysisJobRepository
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repository_interfaces import IAnalysisJobRepository
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus


class AnalysisJobRepository(IAnalysisJobRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, job_id: uuid.UUID) -> AnalysisJob | None:
        return await self._session.get(AnalysisJob, job_id)

    async def get_by_idempotency_key(
        self, document_id: uuid.UUID, idempotency_key: str
    ) -> AnalysisJob | None:
        """Найти job по (document_id, idempotency_key). Возвращает None, если не найден."""
        result = await self._session.execute(
            select(AnalysisJob).where(
                AnalysisJob.document_id == document_id,
                AnalysisJob.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_document_id(self, document_id: uuid.UUID) -> AnalysisJob | None:
        result = await self._session.execute(
            select(AnalysisJob)
            .where(
                AnalysisJob.document_id == document_id,
                AnalysisJob.status.in_((AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)),
            )
            .order_by(AnalysisJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_for_document(self, job: AnalysisJob, document: Document) -> AnalysisJob:
        self._session.add(job)
        document.current_analysis_job_id = job.id
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(job)
        return job

    async def mark_dispatched(self, job: AnalysisJob, document: Document, task_id: str) -> AnalysisJob:
        # C-5: изменения сначала, refresh — после commit
        job.celery_task_id = task_id
        if (
            job.status in (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)
            and document.current_analysis_job_id == job.id
        ):
            document.status = DocumentStatus.IN_PROGRESS
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def mark_failed_queue_unavailable(
        self, job: AnalysisJob, document: Document, message: str | None
    ) -> AnalysisJob:
        job.status = AnalysisJobStatus.FAILED
        job.error_code = "QUEUE_UNAVAILABLE"
        job.error_message = message
        job.finished_at = datetime.now(UTC)
        if document.current_analysis_job_id == job.id:
            document.status = DocumentStatus.DRAFT
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def cancel(self, job: AnalysisJob, document: Document) -> AnalysisJob:
        job.status = AnalysisJobStatus.CANCELLED
        job.error_code = "ANALYSIS_CANCELLED"
        job.error_message = "Анализ отменён"
        job.finished_at = datetime.now(UTC)
        if document.current_analysis_job_id == job.id:
            document.status = DocumentStatus.DRAFT
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def mark_processing_if_active(self, job_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            update(AnalysisJob)
            .where(
                AnalysisJob.id == job_id,
                AnalysisJob.status.in_((AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)),
            )
            .values(
                status=AnalysisJobStatus.PROCESSING,
                started_at=func.coalesce(AnalysisJob.started_at, datetime.now(UTC)),
            )
        )
        await self._session.commit()
        return bool(result.rowcount)

    async def update_status(
        self,
        job: AnalysisJob,
        status: AnalysisJobStatus,
    ) -> AnalysisJob:
        """Сохранить уже изменённый job в БД.

        Q-4: timestamps (started_at, finished_at) управляются в
        AnalysisJobService.update_job_status — репозиторий только persist.
        """
        job.status = status
        await self._session.commit()
        await self._session.refresh(job)
        return job
