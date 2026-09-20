"""
Сервис управления заданиями анализа.

ИСПРАВЛЕНО (review #4, #9, #11):
- Добавлен _get_document_or_raise — единый хелпер для get_by_id + ownership check.
  Устраняет дублирование этого паттерна в 4 местах.
- Бизнес-логика переходов статусов (mark_dispatched, mark_job_queue_unavailable,
  cancel_job) перенесена из репозитория в сервис: сервис мутирует поля job/document,
  репозиторий только вызывает save_with_document / save.
- find_job_by_idempotency_key помечен как внутренний хелпер (убран как публичный
  метод — вся идемпотентность обработана внутри create_job).
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus
from app.infrastructure.db.repositories.analysis_job_repository import AnalysisJobRepository
from app.infrastructure.db.repositories.document_repository import DocumentRepository

# Статусы документа, из которых разрешён запуск анализа:
#   DRAFT              — первичный / повторный запуск (переходы №2, №7→2)
#   AWAITING_APPROVAL  — повторный запуск после изменений (переход №9)
#   READY              — разрешён только с force=True (#9 роутер)
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
    ) -> None:
        self._jobs = analysis_job_repository
        self._documents = document_repository

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_document_or_raise(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        """Загрузить документ и проверить принадлежность проекту.

        Выбрасывает DocumentNotFoundError, если документ не найден
        или не принадлежит указанному проекту.
        """
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return document

    # ------------------------------------------------------------------
    # Core job lifecycle
    # ------------------------------------------------------------------

    async def create_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str | None = None,
    ) -> AnalysisJob:
        """Создать задачу анализа.

        Разрешённые исходные статусы документа (таблица переходов):
          • DRAFT             → переход №2 (первичный/ручной запуск)
          • AWAITING_APPROVAL → переход №9 (повторный запуск)
          • READY             → разрешён только с force=True (#9),
                               роутер проверяет это до вызова create_job

        При повторном запуске из AWAITING_APPROVAL/READY документ сбрасывается
        в DRAFT (пайплайн всегда стартует из DRAFT → IN_PROGRESS).
        """
        document = await self._get_document_or_raise(project_id, document_id)

        # Idempotency-check (P0-7): вернуть существующий job без создания дубля
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
        except IntegrityError as exc:
            if previous_status != DocumentStatus.DRAFT:
                await self._documents.update_status(document, previous_status)
            raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ") from exc
        except Exception:
            if previous_status != DocumentStatus.DRAFT:
                await self._documents.update_status(document, previous_status)
            raise

    async def mark_dispatched(self, job: AnalysisJob, task_id: str) -> AnalysisJob:
        """Зафиксировать отправку задачи в очередь: проставить celery_task_id и
        перевести документ в IN_PROGRESS, если job ещё активен.
        """
        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
        await self._session_refresh(job)
        job.celery_task_id = task_id
        if (
            job.status in (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)
            and document.current_analysis_job_id == job.id
        ):
            document.status = DocumentStatus.IN_PROGRESS
        return await self._jobs.save_with_document(job, document)

    async def mark_job_queue_unavailable(
        self, job: AnalysisJob, error_message: str | None = None
    ) -> AnalysisJob:
        """Очередь недоступна — задача не поставлена, документ остаётся в DRAFT."""
        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
        job.status = AnalysisJobStatus.FAILED
        job.error_code = "QUEUE_UNAVAILABLE"
        job.error_message = error_message
        job.finished_at = datetime.now(UTC)
        if document.current_analysis_job_id == job.id:
            document.status = DocumentStatus.DRAFT
        return await self._jobs.save_with_document(job, document)

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
        job.status = AnalysisJobStatus.CANCELLED
        job.error_code = "ANALYSIS_CANCELLED"
        job.error_message = "Анализ отменён"
        job.finished_at = datetime.now(UTC)
        if document.current_analysis_job_id == job.id:
            document.status = DocumentStatus.DRAFT
        return await self._jobs.save_with_document(job, document)

    async def get_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID, job_id: uuid.UUID
    ) -> AnalysisJob:
        job = await self._jobs.get_by_id(job_id)
        if job is None or job.document_id != document_id:
            raise DocumentNotFoundError(
                f"Задача анализа {job_id} не найдена для документа {document_id}"
            )
        # Ownership check через _get_document_or_raise
        await self._get_document_or_raise(project_id, document_id)
        return job

    # ------------------------------------------------------------------
    # Bulk (#10)
    # ------------------------------------------------------------------

    async def bulk_create_jobs_for_project(
        self, project_id: uuid.UUID
    ) -> list[dict]:
        """Запустить анализ для всех документов проекта в статусе draft/awaiting_approval.

        Возвращает list[dict] вида:
          {"document_id": UUID, "job": AnalysisJob}           — успешный запуск
          {"document_id": UUID, "job": None, "error": str}    — ошибка

        Ошибка для одного документа не блокирует остальные.
        """
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

    # ------------------------------------------------------------------
    # Internal: session refresh helper
    # ------------------------------------------------------------------

    async def _session_refresh(self, obj: object) -> None:
        """Освежить ORM-объект из БД (делегирует репозиторию через сессию)."""
        await self._jobs._session.refresh(obj)
