import uuid

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository


class AnalysisJobService:
    def __init__(
        self, analysis_job_repository: AnalysisJobRepository, document_repository: DocumentRepository
    ):
        self._jobs = analysis_job_repository
        self._documents = document_repository

    async def create_job(self, project_id: uuid.UUID, document_id: uuid.UUID) -> AnalysisJob:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        if document.status not in (DocumentStatus.DRAFT, DocumentStatus.READY):
            raise InvalidDocumentStatusError(
                "Анализ можно запустить только для документа в статусе 'draft' или 'ready'"
            )
        if await self._jobs.get_active_by_document_id(document.id) is not None:
            raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ")
        job = AnalysisJob(id=uuid.uuid4(), document_id=document.id, status=AnalysisJobStatus.PENDING)
        return await self._jobs.create_for_document(job, document)

    async def bulk_create_jobs_for_project(self, project_id: uuid.UUID) -> list[tuple[uuid.UUID, AnalysisJob | None, str | None]]:
        """Запустить анализ для всех документов проекта в статусах draft/ready.

        Возвращает список кортежей (document_id, job, error_code), где job=None при ошибке.
        Ошибка создания задачи для одного документа не блокирует остальные.
        """
        analyzable_documents = await self._documents.list_analyzable_for_project(project_id)
        results: list[tuple[uuid.UUID, AnalysisJob | None, str | None]] = []
        for document in analyzable_documents:
            try:
                job = await self.create_job(project_id, document.id)
                results.append((document.id, job, None))
            except (DocumentNotFoundError, InvalidDocumentStatusError, AnalysisAlreadyRunningError) as exc:
                results.append((document.id, None, exc.__class__.__name__))
        return results
