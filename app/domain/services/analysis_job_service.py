"""
Бизнес-логика задач анализа.

ИСПРАВЛЕНО (code-review):
- C-3: create_job не делает двойной SELECT document — проверка idempotency_key
        переиспользует уже загруженный объект
- A-1: конструктор принимает IAnalysisJobRepository / IDocumentRepository
- P-3: bulk_create_jobs_for_project использует asyncio.gather вместо
        последовательных await — параллельный запуск N задач
- Q-1: исправлены опечатки в docstring (N→Н, V→В, C→С, Z→З)
- Q-4: логика started_at/finished_at перенесена из репозитория в update_status
- Q-7: bulk_create_jobs_for_project возвращает list[BulkJobResult]
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.domain.interfaces.repository_interfaces import IAnalysisJobRepository, IDocumentRepository
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus

_ANALYSIS_ALLOWED_STATUSES = (
    DocumentStatus.DRAFT,
    DocumentStatus.AWAITING_APPROVAL,
    DocumentStatus.READY,
)


@dataclass
class BulkJobResult:
    """Q-7: типизированный результат bulk_create_jobs_for_project."""

    document_id: uuid.UUID
    job: AnalysisJob | None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.job is not None


class AnalysisJobService:
    def __init__(
        self,
        analysis_job_repository: IAnalysisJobRepository,
        document_repository: IDocumentRepository,
    ):
        self._jobs = analysis_job_repository
        self._documents = document_repository

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    async def get_document_for_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        """Вернуть ORM-документ для проверки статуса (#9).

        Используется роутером до create_job, чтобы проверить READY-гард
        без force=True до каких-либо изменений в БД.
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
          • READY             → разрешен только с force=True (#9),
                               роутер проверяет это до вызова create_job

        C-3: один SELECT на document — idempotency-check переиспользует
        уже загруженный объект, без повторного get_by_id.
        """
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )

        # C-3: idempotency-check без повторного SELECT document
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

    # ------------------------------------------------------------------
    # Q-4: update_status с бизнес-логикой timestamps (перенесено из репо)
    # ------------------------------------------------------------------

    async def update_job_status(
        self,
        job: AnalysisJob,
        status: AnalysisJobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AnalysisJob:
        """Обновить статус задачи с управлением timestamps.

        Q-4: бизнес-правило «когда ставить started_at/finished_at» — здесь,
        в сервисе, а не в репозитории.
        """
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        job.status = status
        job.error_code = error_code
        job.error_message = error_message
        if status == AnalysisJobStatus.PROCESSING and job.started_at is None:
            job.started_at = now
        if status in (AnalysisJobStatus.SUCCESS, AnalysisJobStatus.FAILED, AnalysisJobStatus.CANCELLED):
            job.finished_at = now
        return await self._jobs.update_status(job, status)

    # ------------------------------------------------------------------
    # Bulk (#10)
    # ------------------------------------------------------------------

    async def bulk_create_jobs_for_project(
        self, project_id: uuid.UUID
    ) -> list[BulkJobResult]:
        """Запустить анализ для всех документов проекта в статусе draft/awaiting_approval.

        Q-7: возвращает list[BulkJobResult] вместо list[dict].
        P-3: asyncio.gather — параллельный запуск, не последовательный.
        Ошибка для одного документа не блокирует остальные.
        """
        analyzable_documents = await self._documents.list_analyzable_for_project(project_id)

        async def _create_one(document: Document) -> BulkJobResult:
            try:
                job = await self.create_job(project_id, document.id)
                return BulkJobResult(document_id=document.id, job=job)
            except (
                DocumentNotFoundError,
                InvalidDocumentStatusError,
                AnalysisAlreadyRunningError,
            ) as exc:
                return BulkJobResult(
                    document_id=document.id,
                    job=None,
                    error=exc.__class__.__name__,
                )

        # P-3: параллельный запуск всех задач
        results = await asyncio.gather(*(_create_one(doc) for doc in analyzable_documents))
        return list(results)
