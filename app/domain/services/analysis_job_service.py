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

        При повторном запуске из AWAITING_APPROVAL документ сначала сбрасывается
        в DRAFT (пайплайн всегда стартует из DRAFT → IN_PROGRESS). Если после
        сброса создание job упало до коммита — документ откатывается обратно в
        AWAITING_APPROVAL, чтобы пользователь не потерял правки предыдущего раунда
        и мог либо завершить review, либо повторить запуск позже.

        Если сброс и создание job прошли успешно, но Celery-очередь недоступна —
        вызывающий код вызывает mark_job_queue_unavailable; документ остаётся в
        DRAFT (переход №3), что корректно.
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

        # Запоминаем предыдущий статус для отката при повторном запуске из
        # AWAITING_APPROVAL. При запуске из DRAFT откат не нужен — previous_status
        # совпадает с целевым DRAFT.
        previous_status = document.status

        # Приводим документ к DRAFT — пайплайн всегда стартует из этого статуса.
        if document.status != DocumentStatus.DRAFT:
            document = await self._documents.update_status(document, DocumentStatus.DRAFT)

        job = AnalysisJob(id=uuid.uuid4(), document_id=document.id, status=AnalysisJobStatus.PENDING)
        try:
            return await self._jobs.create_for_document(job, document)
        except IntegrityError as exc:
            # Параллельный запрос успел создать active job — откатываем статус
            # документа обратно в предыдущий, чтобы не потерять правки.
            if previous_status != DocumentStatus.DRAFT:
                await self._documents.update_status(document, previous_status)
            raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ") from exc
        except Exception:
            # Неожиданная ошибка при создании job — откатываем статус, чтобы
            # документ не завис в DRAFT без активной задачи.
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

    async def bulk_create_jobs_for_project(
        self, project_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, AnalysisJob | None, str | None]]:
        """Запустить анализ для всех документов проекта в статусах draft/awaiting_approval.

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
