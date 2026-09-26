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
    Ранее метод возвращал job.id (UUID), что делало вызов _job_response(existing)
    в роутере падающим — AnalysisJobResponse.model_validate(UUID) бросает ValidationError.
    Контракт выровнен: метод возвращает AnalysisJob, роутер получает объект напрямую.
  - N-4 (ревью): удалён импорт DocumentStatus (ORM-enum) из роутера.
    Сравнение статуса перенесено в check_document_is_ready() — выполняется внутри
    открытой сессии, использует DocumentStatusVO (доменный тип).
  - N-5 (ревью): check_document_is_ready() возвращает bool, а не ORM-объект.
    Роутер не получает detached ORM-атрибутов за пределами сессии —
    DetachedInstanceError невозможен.
  - N-2 (ревью): добавлен revoke_celery_task() — тонкий делегат к Celery,
    изолирует инфраструктурный импорт celery_app внутри сервиса.
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
from app.domain.value_objects import AnalysisJobStatusVO, DocumentStatusVO

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
        """N-3: возвращает полный AnalysisJob | None.

        Ранее (M-1) метод возвращал job.id (UUID) — это ломало роутер, который
        передавал результат напрямую в _job_response() → model_validate(job).
        Контракт исправлен: возвращается ORM-объект, совместимый с AnalysisJobResponse.
        """
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
        """N-4/N-5: проверить, что документ в статусе READY, не выходя из сессии.

        Возвращает True если документ существует и status == DocumentStatusVO.READY.
        Сравнение выполняется внутри `async with self._uow` — сессия открыта,
        DetachedInstanceError невозможен.
        Роутер получает только bool — никакой ORM-объект не передаётся за пределы сессии.

        Заменяет get_document_for_job() для цели проверки статуса перед созданием job.
        Бросает DocumentNotFoundError если документ не найден в проекте.
        """
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            # N-4: сравниваем с DocumentStatusVO (domain), не с ORM DocumentStatus
            return document.status == DocumentStatusVO.READY

    async def get_document_for_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> uuid.UUID:
        """Проверить, что документ существует в проекте, вернуть его id.

        Оставлен для совместимости с другими вызывающими сторонами.
        Возвращает document_id (UUID) — не ORM-объект.
        Роутер использует check_document_is_ready() для P0-9 guard.
        """
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
        """Создать задачу анализа."""
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
        """HIGH-1: явная проверка job.status == PENDING перед делегированием."""
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
        """N-2: изолирует инфраструктурный импорт celery_app внутри сервиса.

        Роутер вызывает этот метод вместо прямого обращения к celery_app.control.revoke().
        Импорт celery_app выполняется лениво (внутри метода), чтобы не нарушать
        архитектурное правило «domain-сервисы не импортируют инфраструктуру при загрузке».
        В тестах метод можно замокать без поднятия Celery-брокера.
        """
        from app.workers.celery_app import celery_app  # noqa: PLC0415 — lazy import
        celery_app.control.revoke(celery_task_id, terminate=False)

    async def get_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> "AnalysisJob":
        async with self._uow:
            return await self._get_job(project_id, document_id, job_id)

    async def bulk_create_jobs_for_project(
        self, project_id: uuid.UUID
    ) -> list[dict]:
        """CRIT-1: загружаем только document.id (UUID), не ORM-объекты."""
        async with self._uow:
            analyzable_ids: list[uuid.UUID] = [
                doc.id
                for doc in await self._uow.documents.list_analyzable_for_project(
                    project_id
                )
            ]

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
                results.append(
                    {
                        "document_id": document_id,
                        "error": str(exc),
                    }
                )
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
        """CRIT-2: бросает AnalysisJobNotFoundError если job не найден."""
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
