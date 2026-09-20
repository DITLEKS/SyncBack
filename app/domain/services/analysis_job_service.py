import uuid

from sqlalchemy.exc import IntegrityError

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository

# Статусы документа, из которых разрешён запуск анализа:
#   DRAFT              — первичный / повторный запуск (переходы №2, №7→2)
#   AWAITING_APPROVAL  — повторный запуск после изменений (переход №9 по таблице)
_ANALYSIS_ALLOWED_STATUSES = (DocumentStatus.DRAFT, DocumentStatus.AWAITING_APPROVAL)


class AnalysisJobService:
    def __init__(
        self, analysis_job_repository: AnalysisJobRepository, document_repository: DocumentRepository
    ):
        self._jobs = analysis_job_repository
        self._documents = document_repository

    async def create_job(self, project_id: uuid.UUID, document_id: uuid.UUID) -> AnalysisJob:
        """Создать задачу анализа.

        Разрешённые исходные статусы документа (таблица переходов):
          • DRAFT             → переход №2 (первичный/ручной запуск)
          • AWAITING_APPROVAL → переход №9 (повторный запуск)

        Перед созданием job документ переводится в DRAFT, чтобы пайплайн всегда
        следовал маршруту DRAFT → IN_PROGRESS, а не AWAITING_APPROVAL → IN_PROGRESS.
        Это упрощает логику _start_job и исключает попадание в неконсистентный статус
        при ошибке постановки задачи в очередь (переход №3 — остаться/вернуться в DRAFT).
        """
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        if document.status not in _ANALYSIS_ALLOWED_STATUSES:
            raise InvalidDocumentStatusError(
                f"Анализ можно запустить только для документа в статусе "
                f"{' или '.join(s.value for s in _ANALYSIS_ALLOWED_STATUSES)}, "
                f"текущий статус: {document.status.value}"
            )
        if await self._jobs.get_active_by_document_id(document.id) is not None:
            raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ")
        # Приводим документ к DRAFT — пайплайн всегда стартует из этого статуса.
        # При повторном запуске из AWAITING_APPROVAL это сбрасывает предыдущий статус
        # ожидания до того, как новый job будет поставлен в очередь.
        if document.status != DocumentStatus.DRAFT:
            document = await self._documents.update_status(document, DocumentStatus.DRAFT)
        job = AnalysisJob(id=uuid.uuid4(), document_id=document.id, status=AnalysisJobStatus.PENDING)
        try:
            return await self._jobs.create_for_document(job, document)
        except IntegrityError as exc:
            raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ") from exc

    async def mark_dispatched(self, job: AnalysisJob, task_id: str) -> AnalysisJob:
        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
        return await self._jobs.mark_dispatched(job, document, task_id)

    async def mark_job_queue_unavailable(
        self, job: AnalysisJob, error_message: str | None = None
    ) -> AnalysisJob:
        """Очередь недоступна — задача не поставлена, документ остаётся в DRAFT (переход №3)."""
        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
        return await self._jobs.mark_failed_queue_unavailable(job, document, error_message)

    async def cancel_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID, job_id: uuid.UUID
    ) -> AnalysisJob:
        """Отменить активную задачу анализа (переход №7 → документ возвращается в DRAFT)."""
        job = await self.get_job(project_id, document_id, job_id)
        if job.status == AnalysisJobStatus.CANCELLED:
            return job
        if job.status not in (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING):
            raise AnalysisJobNotCancellableError("Завершённую задачу анализа отменить нельзя")
        document = await self._documents.get_by_id(document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {document_id} не найден")
        return await self._jobs.cancel(job, document)

    async def get_job(self, project_id: uuid.UUID, document_id: uuid.UUID, job_id: uuid.UUID) -> AnalysisJob:
        job = await self._jobs.get_by_id(job_id)
        if job is None or job.document_id != document_id:
            raise DocumentNotFoundError(f"Задача анализа {job_id} не найдена для документа {document_id}")
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        return job
