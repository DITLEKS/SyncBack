"""Бизнес-логика задач анализа документов.

Сервис зависит от IUnitOfWork и порта очереди AnalysisQueue; каждая операция —
одна транзакция с одним commit. Переходы статуса документа применяются только
если задача остаётся текущей для документа (current_analysis_job_id).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    AnalysisJobNotFoundError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.domain.interfaces.analysis_queue import AnalysisQueue
from app.domain.interfaces.entities import DocumentProtocol
from app.domain.interfaces.event_publisher import IEventPublisher
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.lifecycle import AnalysisJobLifecycle, DocumentLifecycle
from app.domain.services.document_events import DocumentEventOutbox
from app.domain.value_objects import AnalysisJobStatusVO, DocumentStatusVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.document import Document

logger = logging.getLogger("syncscribe.services.analysis_job")


@dataclass
class ResetResult:
    """Результат сброса ревью: сколько правок вернулось в PENDING и актуальный документ."""

    reset_count: int
    document: DocumentProtocol


class AnalysisJobService:
    def __init__(
        self,
        uow: IUnitOfWork,
        queue: AnalysisQueue | None = None,
        *,
        events: IEventPublisher | None = None,
    ) -> None:
        self._uow = uow
        self._queue = queue
        self._outbox = DocumentEventOutbox(events)

    def _require_queue(self) -> AnalysisQueue:
        if self._queue is None:
            raise RuntimeError("AnalysisJobService создан без очереди анализа")
        return self._queue

    # ------------------------------------------------------------------
    # Idempotency helpers
    # ------------------------------------------------------------------

    async def find_job_by_idempotency_key(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str,
    ) -> AnalysisJob | None:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                return None
            return await self._uow.jobs.get_by_idempotency_key(document_id, idempotency_key)

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    async def check_document_needs_force_confirm(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> bool:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            return DocumentLifecycle.analysis_needs_confirmation(document.status)

    # ------------------------------------------------------------------
    # Core job lifecycle
    # ------------------------------------------------------------------

    async def create_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str | None = None,
    ) -> AnalysisJob:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )

            if idempotency_key is not None:
                existing = await self._uow.jobs.get_by_idempotency_key(document_id, idempotency_key)
                if existing is not None:
                    return existing

            if not DocumentLifecycle.can_start_analysis(document.status):
                raise InvalidDocumentStatusError(
                    f"Анализ нельзя запустить для документа в статусе {document.status.value}"
                )

            if await self._uow.jobs.get_active_by_document_id(document.id) is not None:
                raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ")

            # Пока задача не поставлена в очередь, документ — черновик: прежние
            # результаты ревью считаются сброшенными.
            if document.status != DocumentStatusVO.DRAFT:
                document = await self._set_document_status(document, DocumentStatusVO.DRAFT)

            job = await self._uow.jobs.create_for_document(
                document,
                status=AnalysisJobStatusVO.PENDING,
                idempotency_key=idempotency_key,
            )
            await self._uow.documents.set_current_job(document, job.id)
            await self._commit()
        return job

    async def dispatch_job(self, job: AnalysisJob) -> AnalysisJob:
        """Отправить задачу в очередь и вернуть её актуальное состояние.

        Статус dispatched выставляется до отправки, чтобы быстрый воркер не был
        перезаписан более поздней записью. Если очередь недоступна, задача
        помечается FAILED (QUEUE_UNAVAILABLE), документ возвращается в DRAFT,
        и такая задача возвращается без исключения.
        """
        if not AnalysisJobLifecycle.can_dispatch(job.status):
            raise InvalidDocumentStatusError(
                f"Задачу анализа в статусе {job.status.value} нельзя отправить в очередь"
            )
        queue = self._require_queue()

        async with self._uow:
            document = await self._uow.documents.get_by_id(job.document_id)
            if document is None:
                raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
            sources = await self._uow.sources.list_for_analysis(document.project_id, document.id)
            source_ids = [source.id for source in sources]
            job = await self._set_job_status(job, document, AnalysisJobStatusVO.DISPATCHED)
            await self._commit()

        try:
            task_id = await queue.enqueue(job.id, source_ids)
        except Exception as exc:  # noqa: BLE001 — любой сбой брокера фиксируем в задаче
            logger.warning(
                "Очередь анализа недоступна, задача помечена FAILED",
                exc_info=True,
                extra={"job_id": str(job.id), "document_id": str(job.document_id)},
            )
            return await self._mark_queue_unavailable(job, str(exc))

        async with self._uow:
            job = await self._uow.jobs.set_celery_task_id(job, task_id)
            await self._commit()
        return job

    async def _mark_queue_unavailable(self, job: AnalysisJob, error_message: str) -> AnalysisJob:
        async with self._uow:
            document = await self._uow.documents.get_by_id(job.document_id)
            if document is None:
                raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
            job = await self._set_job_status(
                job,
                document,
                AnalysisJobStatusVO.FAILED,
                error_code="QUEUE_UNAVAILABLE",
                error_message=error_message,
            )
            await self._commit()
        return job

    async def cancel_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> AnalysisJob:
        async with self._uow:
            job = await self._get_job(project_id, document_id, job_id)
            if job.status == AnalysisJobStatusVO.CANCELLED:
                return job
            if not AnalysisJobLifecycle.can_cancel(job.status):
                raise AnalysisJobNotCancellableError("Завершённую задачу анализа отменить нельзя")
            document = await self._uow.documents.get_by_id(document_id)
            if document is None:
                raise DocumentNotFoundError(f"Документ {document_id} не найден")
            job = await self._set_job_status(
                job,
                document,
                AnalysisJobStatusVO.CANCELLED,
                error_code="ANALYSIS_CANCELLED",
                error_message="Анализ отменён",
            )
            await self._commit()
        return job

    async def revoke_celery_task(self, celery_task_id: str) -> None:
        await self._require_queue().revoke(celery_task_id)

    async def get_active_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> AnalysisJob | None:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                return None
            return await self._uow.jobs.get_active_by_document_id(document_id)

    async def get_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> AnalysisJob:
        async with self._uow:
            return await self._get_job(project_id, document_id, job_id)

    async def reset_analysis(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> ResetResult:
        """Вернуть все правки текущего анализа в PENDING, документ — в AWAITING_APPROVAL."""
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            if not DocumentLifecycle.can_reset_review(document.status):
                raise InvalidDocumentStatusError(
                    f"Сброс невозможен для документа в статусе {document.status.value}"
                )
            if document.current_analysis_job_id is None:
                raise AnalysisJobNotFoundError("У документа нет активного анализа для сброса")

            reset_ids = await self._uow.suggestions.reset_to_pending(
                document.current_analysis_job_id
            )
            document = await self._set_document_status(document, DocumentStatusVO.AWAITING_APPROVAL)
            await self._commit()

        return ResetResult(reset_count=len(reset_ids), document=document)

    async def bulk_create_jobs_for_project(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID] | None = None,
    ) -> list[dict]:
        """Создать задачи для всех анализируемых документов проекта (или только document_ids)."""
        async with self._uow:
            analyzable_docs = await self._uow.documents.list_by_statuses(
                project_id, DocumentLifecycle.auto_analyzable_statuses()
            )
            all_analyzable = [doc.id for doc in analyzable_docs]

        if document_ids is not None:
            requested = frozenset(document_ids)
            analyzable_ids = [did for did in all_analyzable if did in requested]
        else:
            analyzable_ids = all_analyzable

        results: list[dict] = []
        for document_id in analyzable_ids:
            try:
                job = await self.create_job(project_id, document_id)
                results.append({"document_id": document_id, "job": job})
            except (
                DocumentNotFoundError,
                InvalidDocumentStatusError,
                AnalysisAlreadyRunningError,
            ) as exc:
                results.append({"document_id": document_id, "error": str(exc)})
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _set_document_status(self, document: Document, target: DocumentStatusVO) -> Document:
        document = await self._uow.documents.update_status(
            document, DocumentLifecycle.transition(document.status, target)
        )
        self._outbox.record(document)
        return document

    async def _commit(self) -> None:
        try:
            await self._uow.commit()
        except BaseException:
            self._outbox.discard()
            raise
        await self._outbox.flush(self._uow)

    async def _set_job_status(
        self,
        job: AnalysisJob,
        document: Document,
        target: AnalysisJobStatusVO,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AnalysisJob:
        """Сменить статус задачи и, если она текущая для документа, статус документа."""
        job = await self._uow.jobs.update_status(
            job,
            AnalysisJobLifecycle.transition(job.status, target),
            error_code=error_code,
            error_message=error_message,
        )
        if document.current_analysis_job_id == job.id:
            await self._set_document_status(
                document, AnalysisJobLifecycle.document_status_for(target)
            )
        return job

    async def _get_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> AnalysisJob:
        document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        job = await self._uow.jobs.get_by_id(job_id)
        if job is None or job.document_id != document_id:
            raise AnalysisJobNotFoundError(
                f"Задача {job_id} не найдена для документа {document_id}"
            )
        return job
