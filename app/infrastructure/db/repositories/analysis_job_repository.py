import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.enums import AnalysisJobStatus


class AnalysisJobRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, job_id: uuid.UUID) -> AnalysisJob | None:
        return await self._session.get(AnalysisJob, job_id)

    async def create(self, job: AnalysisJob) -> AnalysisJob:
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def update_celery_task_id(self, job: AnalysisJob, celery_task_id: str) -> AnalysisJob:
        job.celery_task_id = celery_task_id
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def mark_failed_queue_unavailable(self, job: AnalysisJob, error_message: str | None = None) -> AnalysisJob:
        return await self.update_status(job, AnalysisJobStatus.FAILED, error_code="QUEUE_UNAVAILABLE", error_message=error_message)

    async def update_status(self, job: AnalysisJob, status: AnalysisJobStatus, error_code: str | None = None, error_message: str | None = None) -> AnalysisJob:
        job.status = status
        job.error_code = error_code
        job.error_message = error_message
        now = datetime.now(UTC)
        if status == AnalysisJobStatus.PROCESSING and job.started_at is None:
            job.started_at = now
        if status in (AnalysisJobStatus.SUCCESS, AnalysisJobStatus.FAILED):
            job.finished_at = now
        await self._session.commit()
        await self._session.refresh(job)
        return job
