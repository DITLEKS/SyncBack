"""
Бизнес-логика задач анализа документов.

Архитектурные правила:
  - Сервис зависит только от IUnitOfWork — не от конкретных репозиториев.
  - Один uow.commit() на операцию (кроме компенсирующих транзакций при ошибках).
  - H-2: ORM-объект AnalysisJob создаётся внутри репозитория через фабричный метод.
  - Никаких импортов из app.infrastructure.* при выполнении (НЕ TYPE_CHECKING).
  - CRIT-A/B: mark_dispatched / mark_job_queue_unavailable / cancel_job адаптированы
    под новую сигнатуру репозитория: методы больше не принимают document напрямую,
    а возвращают (job, DocumentStatusVO | None). Сервис применяет изменение
    документа через uow.documents.update_status().
  - CRIT-NEW-1: IntegrityError перехватывается в репозитории и транслируется
    в AnalysisAlreadyRunningError — инфраструктурные исключения в domain недопустимы.

ИСПРАВЛЕНИЯ:
  - CRIT-1: bulk_create_jobs_for_project загружает только document.id (list[UUID]),
    не ORM-объекты. Объекты из первого UoW были бы detached при повторном входе в uow.
  - CRIT-2: _get_job бросает AnalysisJobNotFoundError при ненайденном job,
    а не DocumentNotFoundError — правильная семантика для API 404.
  - HIGH-1: mark_dispatched явно проверяет job.status == PENDING перед делегированием
    в репозиторий; диспатч завершённой или обработанной задачи — логическая ошибка.
  - M-1: find_job_by_idempotency_key возвращает job.id (UUID), не ORM-объект,
    чтобы избежать DetachedInstanceError после выхода из async with self._uow.
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
    from app.infrastructure.db.models.document import Document

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
    ) -> uuid.UUID | None:
        """Найти job по ключу идемпотентности.

        M-1: возвращает job.id (UUID), а не ORM-объект — после выхода из
        async with self._uow сессия закрыта и ORM-объект стал бы detached.
        Вызывающий код должен перезагрузить job через get_job() если нужен
        полный объект.
        """
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                return None
            job = await self._uow.jobs.get_by_idempotency_key(
                document_id, idempotency_key
            )
            return job.id if job is not None else None

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    async def get_document_for_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> "Document":
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
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
    ) -> "AnalysisJob":
        """Создать задачу анализа.

        Одна транзакция:
          1. Проверка статуса документа
          2. Idempotency-check (если ключ передан)
          3. Сброс документа в DRAFT (если не DRAFT)
          4. INSERT job + UPDATE document.current_analysis_job_id  (фабрика в репозитории)
          5. commit

        CRIT-NEW-1: IntegrityError больше не перехватывается здесь.
        Репозиторий обязан поймать sqlalchemy.exc.IntegrityError
        и выбросить AnalysisAlreadyRunningError сам.
        """
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

            # H-2: ORM-объект создаётся внутри репозитория — сервис не знает про AnalysisJob ORM.
            # Репозиторий перехватывает IntegrityError и бросает AnalysisAlreadyRunningError.
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
        """Отметить job как отправленный в Celery.

        HIGH-1: явная проверка job.status == PENDING перед делегированием.
        Диспатч завершённой/обработанной задачи — логическая ошибка и должен
        быть отклонён на уровне сервиса, не репозитория.

        CRIT-A: репозиторий больше не принимает document. Сервис загружает
        document самостоятельно и применяет new_doc_status через uow.documents.update_status().
        """
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
        """Отметить job как недоступный из-за недоступности очереди.

        CRIT-B: репозиторий больше не принимает document. Сервис загружает document
        самостоятельно и применяет new_doc_status через uow.documents.update_status().
        """
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
        """Отменить задачу анализа.

        CRIT-B: репозиторий больше не принимает document. Сервис загружает document
        самостоятельно и применяет new_doc_status через uow.documents.update_status().
        """
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
        """Запустить анализ для всех документов проекта в статусе draft/awaiting_approval.

        CRIT-1: загружаем только document.id (UUID), не ORM-объекты.
        После выхода из первого async with self._uow сессия закрыта — любые
        ORM-объекты стали бы detached и обращение к их атрибутам в цикле
        вызвало бы DetachedInstanceError.

        Каждый документ — отдельный UoW, чтобы ошибка одного
        не откатывала остальных.
        """
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
                        "job": None,
                        "error": exc.__class__.__name__,
                    }
                )
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> "AnalysisJob":
        """Проверить принадлежность job → document → project.

        CRIT-2: бросает AnalysisJobNotFoundError если job не найден или не
        принадлежит document_id — не DocumentNotFoundError, который семантически
        означает «документ не найден» и маппится в другой HTTP-ответ.
        """
        job = await self._uow.jobs.get_by_id(job_id)
        if job is None or job.document_id != document_id:
            raise AnalysisJobNotFoundError(
                f"Задача анализа {job_id} не найдена для документа {document_id}"
            )
        document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return job
