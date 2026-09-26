import uuid

from app.domain.enums import AnalysisJobStatus, DocumentStatus
from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    ConcurrencyError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository

_ANALYSIS_ALLOWED_STATUSES = (
    DocumentStatus.DRAFT,
    DocumentStatus.AWAITING_APPROVAL,
    DocumentStatus.READY,
)


class AnalysisJobService:
    def __init__(
        self,
        analysis_job_repository: AnalysisJobRepository,
        document_repository: DocumentRepository,
    ):
        self._jobs = analysis_job_repository
        self._documents = document_repository

    async def find_job_by_idempotency_key(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str,
    ) -> AnalysisJob | None:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            return None
        return await self._jobs.get_by_idempotency_key(document_id, idempotency_key)

    async def get_document_for_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return document

    async def create_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str | None = None,
    ) -> AnalysisJob:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )

        if idempotency_key is not None:
            existing = await self._jobs.get_by_idempotency_key(document_id, idempotency_key)
            if existing is not None:
                return existing

        if document.status not in _ANALYSIS_ALLOWED_STATUSES:
            raise InvalidDocumentStatusError(
                f"Анализ можно запустить только для документа в статусе "
                f"{' или '.join(s.value for s in _ANALYSIS_ALLOWED_STATUSES)}, "
                f"текущий статус: {document.status.value}"
            )
        if await self._jobs.get_active_by_document_id(document.id) is not None:
            raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ")

        previous_status = document.status

        if document.status != DocumentStatus.DRAFT:
            document = await self._documents.update_status(document, DocumentStatus.DRAFT)

        job = AnalysisJob(
            id=uuid.uuid4(),
            document_id=document.id,
            status=AnalysisJobStatus.PENDING,
            idempotency_key=idempotency_key,
        )
        try:
            return await self._jobs.create_for_document(job, document)
        except ConcurrencyError as exc:
            if previous_status != DocumentStatus.DRAFT:
                await self._documents.update_status(document, previous_status)
            raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ") from exc
        except Exception:
            if previous_status != DocumentStatus.DRAFT:
                await self._documents.update_status(document, previous_status)
            raise

    async def mark_dispatched(self, job: AnalysisJob, task_id: str) -> AnalysisJob:
        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
        return await self._jobs.mark_dispatched(job, document, task_id)

    async def mark_job_queue_unavailable(
        self, job: AnalysisJob, error_message: str | None = None
    ) -> AnalysisJob:
        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
        return await self._jobs.mark_failed_queue_unavailable(job, document, error_message)

    async def cancel_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID, job_id: uuid.UUID
    ) -> AnalysisJob:
        job = await self.get_job(project_id, document_id, job_id)
        if job.status == AnalysisJobStatus.CANCELLED:
            return job
        if job.status not in (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING):
            raise AnalysisJobNotCancellableError("Завершённую задачу анализа отменить нельзя")
        document = await self._documents.get_by_id(document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {document_id} не найден")
        return await self._jobs.cancel(job, document)

    async def get_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID, job_id: uuid.UUID
    ) -> AnalysisJob:
        job = await self._jobs.get_by_id(job_id)
        if job is None or job.document_id != document_id:
            raise DocumentNotFoundError(
                f"Задача анализа {job_id} не найдена для документа {document_id}"
            )
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return job

    async def bulk_create_jobs_for_project(
        self, project_id: uuid.UUID
    ) -> list[dict]:
        analyzable_documents = await self._documents.list_analyzable_for_project(project_id)
        results: list[dict] = []
        for document in analyzable_documents:
            try:
                job = await self.create_job(project_id, document.id)
                results.append({"document_id": document.id, "job": job})
            except (
                DocumentNotFoundError,
                InvalidDocumentStatusError,
                AnalysisAlreadyRunningError,
            ) as exc:
                results.append(
                    {
                        "document_id": document.id,
                        "job": None,
                        "error": exc.__class__.__name__,
                    }
                )
        return results
