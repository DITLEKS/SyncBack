"""
Бизнес-логика задач анализа документов.

Архитектурные правила:
  - Сервис зависит только от IUnitOfWork — не от конкретных репозиториев.
  - Один uow.commit() на операцию.
  - H-2: ORM-объект AnalysisJob создаётся внутри репозитория через фабричный метод.
  - Никаких импортов из app.infrastructure.* при выполнении (НЕ TYPE_CHECKING).
  - CRIT-A/B: mark_dispatched / mark_job_queue_unavailable / cancel_job адаптированы
    под новую сигнатуру репозитория.
  - CRIT-NEW-1: IntegrityError перехватывается в репозитории и транслируется
    в AnalysisAlreadyRunningError.

ИСПРАВЛЕНИЯ:
  - CRIT-1: bulk_create_jobs_for_project загружает только document.id (list[UUID]).
  - CRIT-2: _get_job бросает AnalysisJobNotFoundError при ненайденном job.
  - HIGH-1: mark_dispatched явно проверяет job.status == PENDING.
  - N-3 (ревью): find_job_by_idempotency_key возвращает полный AnalysisJob | None.
  - N-4 (ревью): удалён импорт DocumentStatus (ОРМ-enum) из роутера.
  - N-5 (ревью): check_document_is_ready() возвращает bool, а не ORM-объект.
  - N-2 (ревью): добавлен revoke_celery_task() — тонкий делегат к Celery.
  - UI-fix: bulk_create_jobs_for_project принимает опциональный document_ids фильтр.
  - FEAT: reset_analysis() — сброс всех suggestions → pending, документ → AWAITING_APPROVAL.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    AnalysisJobNotFoundError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import AnalysisJobStatusVO, DocumentStatusVO, SuggestionStatusVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob

# Статусы документа, из которых разрешён запуск анализа:
_ANALYSIS_ALLOWED_STATUSES = frozenset({
    DocumentStatusVO.DRAFT,
    DocumentStatusVO.AWAITING_APPROVAL,
    DocumentStatusVO.READY,
})

# Статусы job, из которых допустима отмена:
_CANCELLABLE_JOB_STATUSES = frozenset({
    AnalysisJobStatusVO.PENDING,
    AnalysisJobStatusVO.PROCESSING,
})

# Статусы job, из которых допустим диспатч:
_DISPATCHABLE_JOB_STATUSES = frozenset({
    AnalysisJobStatusVO.PENDING,
})

# Статусы документа, из которых разрешён сброс:
_RESET_ALLOWED_STATUSES = frozenset({
    DocumentStatusVO.AWAITING_APPROVAL,
    DocumentStatusVO.READY,
})


class AnalysisJobService:
    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------
    # Idempotency helpers
    # ------------------------------------------------------------------

    async def find_job_by_idempotency_key(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str,
    ) -> "AnalysisJob | None":
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                return None
            return await self._uow.jobs.get_by_idempotency_key(
                document_id, idempotency_key
            )

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    async def check_document_is_ready(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> bool:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            return document.status == DocumentStatusVO.READY

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
    ) -> "AnalysisJob":
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )

            if idempotency_key is not None:
                existing = await self._uow.jobs.get_by_idempotency_key(
                    document_id, idempotency_key
                )
                if existing is not None:
                    return existing

            if document.status not in _ANALYSIS_ALLOWED_STATUSES:
                raise InvalidDocumentStatusError(
                    f"Анализ можно запустить только для документа в статусе "
                    f"{' или '.join(s.value for s in _ANALYSIS_ALLOWED_STATUSES)}, "
                    f"текущий статус: {document.status}"
                )

            if await self._uow.jobs.get_active_by_document_id(document.id) is not None:
                raise AnalysisAlreadyRunningError(
                    "Для документа уже выполняется анализ"
                )

            if document.status != DocumentStatusVO.DRAFT:
                document = await self._uow.documents.update_status(
                    document, DocumentStatusVO.DRAFT
                )

            job = await self._uow.jobs.create_for_document(
                document,
                status=AnalysisJobStatusVO.PENDING,
                idempotency_key=idempotency_key,
            )
            await self._uow.commit()
        return job

    async def mark_dispatched(
        self, job: "AnalysisJob", task_id: str
    ) -> "AnalysisJob":
        if job.status not in _DISPATCHABLE_JOB_STATUSES:
            raise InvalidDocumentStatusError(
                f"Диспатч недопустим для задачи в статусе {job.status!r}. "
                f"Допустимые статусы: {', '.join(s.value for s in _DISPATCHABLE_JOB_STATUSES)}"
            )
        async with self._uow:
            document = await self._uow.documents.get_by_id(job.document_id)
            if document is None:
                raise DocumentNotFoundError(
                    f"Документ {job.document_id} не найден"
                )
            job, new_doc_status = await self._uow.jobs.mark_dispatched(job, task_id)
            if new_doc_status is not None and document.current_analysis_job_id == job.id:
                await self._uow.documents.update_status(document, new_doc_status)
            await self._uow.commit()
        return job

    async def mark_job_queue_unavailable(
        self, job: "AnalysisJob", error_message: str | None = None
    ) -> "AnalysisJob":
        async with self._uow:
            document = await self._uow.documents.get_by_id(job.document_id)
            if document is None:
                raise DocumentNotFoundError(
                    f"Документ {job.document_id} не найден"
                )
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
    ) -> "AnalysisJob":
        async with self._uow:
            job = await self._get_job(project_id, document_id, job_id)
            if job.status == AnalysisJobStatusVO.CANCELLED:
                return job
            if job.status not in _CANCELLABLE_JOB_STATUSES:
                raise AnalysisJobNotCancellableError(
                    "Завершённую задачу анализа отменить нельзя"
                )
            document = await self._uow.documents.get_by_id(document_id)
            if document is None:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден"
                )
            job, new_doc_status = await self._uow.jobs.cancel(job)
            if document.current_analysis_job_id == job.id:
                await self._uow.documents.update_status(document, new_doc_status)
            await self._uow.commit()
        return job

    async def revoke_celery_task(self, celery_task_id: str) -> None:
        from app.workers.celery_app import celery_app  # noqa: PLC0415 — lazy import
        celery_app.control.revoke(celery_task_id, terminate=False)

    async def get_active_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> "AnalysisJob | None":
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
    ) -> "AnalysisJob":
        async with self._uow:
            return await self._get_job(project_id, document_id, job_id)

    async def reset_analysis(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        """Сбросить все правки текущего job: suggestions → pending,
        документ → AWAITING_APPROVAL.

        Допустимо только из статусов AWAITING_APPROVAL и READY.
        Если у документа нет current_analysis_job_id — сбрасывать нечего,
        выбрасываем AnalysisJobNotFoundError.
        """
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            if document.status not in _RESET_ALLOWED_STATUSES:
                raise InvalidDocumentStatusError(
                    f"Сброс невозможен для документа в статусе {document.status.value}. "
                    f"Допустимые статусы: "
                    + ", ".join(s.value for s in _RESET_ALLOWED_STATUSES)
                )
            if document.current_analysis_job_id is None:
                raise AnalysisJobNotFoundError(
                    "У документа нет активного анализа для сброса"
                )

            # Сбрасываем все правки текущего job обратно в pending
            await self._uow.suggestions.reset_to_pending_by_job(
                document.current_analysis_job_id
            )
            # Документ возвращается в AWAITING_APPROVAL (правки снова требуют ревью)
            await self._uow.documents.update_status(
                document, DocumentStatusVO.AWAITING_APPROVAL
            )
            await self._uow.commit()

    async def bulk_create_jobs_for_project(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID] | None = None,
    ) -> list[dict]:
        """CRIT-1: загружаем только document.id (UUID), не ORM-объекты.

        UI-fix: если document_ids задан — запускаем анализ только для них
        (если они входят в проект). Если None — все analyzable-документы проекта.
        """
        async with self._uow:
            all_analyzable: list[uuid.UUID] = [
                doc.id
                for doc in await self._uow.documents.list_analyzable_for_project(
                    project_id
                )
            ]

        # Фильтрация по document_ids если задана
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
    ) -> "AnalysisJob":
        document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        job = await self._uow.jobs.get_by_id(job_id)
        if job is None or job.document_id != document_id:
            raise AnalysisJobNotFoundError(
                f"Задача {job_id} не найдена для документа {document_id}"
            )
        return job
