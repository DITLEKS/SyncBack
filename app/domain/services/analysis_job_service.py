import uuid

from app.domain.exceptions import DocumentNotFoundError
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository


class AnalysisJobService:
    def __init__(self, analysis_job_repository: AnalysisJobRepository, document_repository: DocumentRepository):
        self._jobs = analysis_job_repository
        self._documents = document_repository

    async def create_job(self, project_id: uuid.UUID, document_id: uuid.UUID) -> AnalysisJob:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        job = AnalysisJob(document_id=document.id)
        return await self._jobs.create(job)

    async def set_celery_task_id(self, job: AnalysisJob, celery_task_id: str) -> AnalysisJob:
        return await self._jobs.update_celery_task_id(job, celery_task_id)

    async def mark_job_queue_unavailable(self, job: AnalysisJob, error_message: str | None = None) -> AnalysisJob:
        return await self._jobs.mark_failed_queue_unavailable(job, error_message)

    async def get_job(self, project_id: uuid.UUID, document_id: uuid.UUID, job_id: uuid.UUID) -> AnalysisJob:
        job = await self._jobs.get_by_id(job_id)
        if job is None or job.document_id != document_id:
            raise DocumentNotFoundError(f"Задача анализа {job_id} не найдена для документа {document_id}")
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        return job
