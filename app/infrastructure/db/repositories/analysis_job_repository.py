import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus


class AnalysisJobRepository:
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
        await self._session.refresh(job)
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
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AnalysisJob:
        job.status = status
        job.error_code = error_code
        job.error_message = error_message
        now = datetime.now(UTC)
        if status == AnalysisJobStatus.PROCESSING and job.started_at is None:
            job.started_at = now
        if status in (AnalysisJobStatus.SUCCESS, AnalysisJobStatus.FAILED, AnalysisJobStatus.CANCELLED):
            job.finished_at = now
        await self._session.commit()
        await self._session.refresh(job)
        return job
