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
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import AnalysisJobStatusVO, DocumentStatusVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob

logger = logging.getLogger("syncscribe.services.analysis_job")

_ANALYSIS_ALLOWED_STATUSES = frozenset(
    {
        DocumentStatusVO.DRAFT,
        DocumentStatusVO.AWAITING_APPROVAL,
        DocumentStatusVO.READY,
    }
)

_FORCE_CONFIRM_STATUSES = frozenset(
    {
        DocumentStatusVO.READY,
    }
)

_CANCELLABLE_JOB_STATUSES = frozenset(
    {
        AnalysisJobStatusVO.PENDING,
        AnalysisJobStatusVO.PROCESSING,
    }
)

_DISPATCHABLE_JOB_STATUSES = frozenset(
    {
        AnalysisJobStatusVO.PENDING,
    }
)

_RESET_ALLOWED_STATUSES = frozenset(
    {
        DocumentStatusVO.AWAITING_APPROVAL,
        DocumentStatusVO.READY,
    }
)


@dataclass
class ResetResult:
    """Результат сброса ревью: сколько правок вернулось в PENDING и актуальный документ."""

    reset_count: int
    document: DocumentProtocol


class AnalysisJobService:
    def __init__(self, uow: IUnitOfWork, queue: AnalysisQueue | None = None) -> None:
        self._uow = uow
        self._queue = queue

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

    async def check_document_is_ready(self, project_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            return document.status == DocumentStatusVO.READY

    async def check_document_needs_force_confirm(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> bool:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            return document.status in _FORCE_CONFIRM_STATUSES

    async def get_document_for_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> uuid.UUID:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            return document_id

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

            if document.status not in _ANALYSIS_ALLOWED_STATUSES:
                raise InvalidDocumentStatusError(
                    f"Анализ можно запустить только для документа в статусе "
                    f"{' или '.join(s.value for s in _ANALYSIS_ALLOWED_STATUSES)}, "
                    f"текущий статус: {document.status}"
                )

            if await self._uow.jobs.get_active_by_document_id(document.id) is not None:
                raise AnalysisAlreadyRunningError("Для документа уже выполняется анализ")

            if document.status != DocumentStatusVO.DRAFT:
                document = await self._uow.documents.update_status(document, DocumentStatusVO.DRAFT)

            job = await self._uow.jobs.create_for_document(
                document,
                status=AnalysisJobStatusVO.PENDING,
                idempotency_key=idempotency_key,
            )
            await self._uow.documents.set_current_job(document, job.id)
            await self._uow.commit()
        return job

    async def dispatch_job(self, job: AnalysisJob) -> AnalysisJob:
        """Отправить задачу в очередь: статусы «в обработке» выставляются до отправки.

        Если очередь недоступна, задача помечается FAILED (QUEUE_UNAVAILABLE),
        документ возвращается в DRAFT; исключение пробрасывается вызывающему.
        """
        if job.status not in _DISPATCHABLE_JOB_STATUSES:
            raise InvalidDocumentStatusError(
                f"Диспатч недопустим для задачи в статусе {job.status!r}. "
                f"Допустимые статусы: {', '.join(s.value for s in _DISPATCHABLE_JOB_STATUSES)}"
            )
        queue = self._require_queue()

        async with self._uow:
            document = await self._uow.documents.get_by_id(job.document_id)
            if document is None:
                raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
            sources = await self._uow.sources.list_for_analysis(document.project_id, document.id)
            source_ids = [source.id for source in sources]
            job, new_doc_status = await self._uow.jobs.mark_dispatched(job)
            if new_doc_status is not None and document.current_analysis_job_id == job.id:
                await self._uow.documents.update_status(document, new_doc_status)
            await self._uow.commit()

        try:
            task_id = await queue.enqueue(job.id, source_ids)
        except Exception as exc:
            logger.warning(
                "Очередь анализа недоступна, задача помечена FAILED",
                exc_info=True,
                extra={"job_id": str(job.id), "document_id": str(job.document_id)},
            )
            await self.mark_job_queue_unavailable(job, str(exc))
            raise

        async with self._uow:
            job = await self._uow.jobs.set_celery_task_id(job, task_id)
            await self._uow.commit()
        return job

    async def mark_job_queue_unavailable(
        self, job: AnalysisJob, error_message: str | None = None
    ) -> AnalysisJob:
        async with self._uow:
            document = await self._uow.documents.get_by_id(job.document_id)
            if document is None:
                raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
            job, new_doc_status = await self._uow.jobs.mark_failed_queue_unavailable(
                job, error_message
            )
            if document.current_analysis_job_id == job.id:
                await self._uow.documents.update_status(document, new_doc_status)
            await self._uow.commit()
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
            if job.status not in _CANCELLABLE_JOB_STATUSES:
                raise AnalysisJobNotCancellableError("Завершённую задачу анализа отменить нельзя")
            document = await self._uow.documents.get_by_id(document_id)
            if document is None:
                raise DocumentNotFoundError(f"Документ {document_id} не найден")
            job, new_doc_status = await self._uow.jobs.cancel(job)
            if document.current_analysis_job_id == job.id:
                await self._uow.documents.update_status(document, new_doc_status)
            await self._uow.commit()
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
            if document.status not in _RESET_ALLOWED_STATUSES:
                raise InvalidDocumentStatusError(
                    f"Сброс невозможен для документа в статусе {document.status.value}. "
                    f"Допустимые статусы: " + ", ".join(s.value for s in _RESET_ALLOWED_STATUSES)
                )
            if document.current_analysis_job_id is None:
                raise AnalysisJobNotFoundError("У документа нет активного анализа для сброса")

            reset_ids = await self._uow.suggestions.reset_to_pending(
                document.current_analysis_job_id
            )
            document = await self._uow.documents.update_status(
                document, DocumentStatusVO.AWAITING_APPROVAL
            )
            await self._uow.commit()

        return ResetResult(reset_count=len(reset_ids), document=document)

    async def bulk_create_jobs_for_project(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID] | None = None,
    ) -> list[dict]:
        """Создать задачи для всех анализируемых документов проекта (или только document_ids)."""
        async with self._uow:
            all_analyzable: list[uuid.UUID] = [
                doc.id for doc in await self._uow.documents.list_analyzable_for_project(project_id)
            ]

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
