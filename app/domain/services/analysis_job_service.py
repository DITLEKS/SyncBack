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
  - N-3 (ревю): find_job_by_idempotency_key возвращает полный AnalysisJob | None.
  - N-4 (ревю): удалён импорт DocumentStatus (ОРМ-enum) из роутера.
  - N-5 (ревю): check_document_is_ready() возвращает bool, а не ORM-объект.
  - N-2 (ревю): добавлен revoke_celery_task() — тонкий делегат к Celery.
  - UI-fix: bulk_create_jobs_for_project принимает опциональный document_ids фильтр.
  - FEAT: reset_analysis() — сброс всех suggestions → pending, документ → AWAITING_APPROVAL.
  - FIX-P0: убраны DocumentStatusVO.ERROR/CANCELLED из _ANALYSIS_ALLOWED_STATUSES
    и _FORCE_CONFIRM_STATUSES — эти значения удалены из DocumentStatusVO в
    коммите fe39c67 (4STATUS). После 4STATUS документ с ошибкой/отменой
    анализа имеет status=DRAFT, что уже входит в оба frozenset.
  - FIX-P0-DISPATCH: добавлен dispatch_job() — строит Celery chord и вызывает
    mark_dispatched(). create_job() теперь устанавливает current_analysis_job_id.
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

# FIX-P0: ERROR/CANCELLED удалены — они не существуют в DocumentStatusVO (4STATUS).
# Документ после сбоя/отмены анализа переводится в DRAFT (миграция 0020),
# поэтому DRAFT здесь достаточно для повторного запуска.
_ANALYSIS_ALLOWED_STATUSES = frozenset({
    DocumentStatusVO.DRAFT,
    DocumentStatusVO.AWAITING_APPROVAL,
    DocumentStatusVO.READY,
})

# FIX-P0: ERROR/CANCELLED удалены. READY остаётся единственным статусом,
# из которого повторный запуск требует явного force=true.
_FORCE_CONFIRM_STATUSES = frozenset({
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
        """Устаревший метод — используйте check_document_needs_force_confirm.

        Оставлен для обратной совместимости; возвращает True только для READY.
        """
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
        """Возвращает True если повторный запуск анализа требует
        явного подтверждения (force=true) от пользователя.

        True для статуса READY — анализ уже выполнялся и перезапуск
        должен быть осознанным.
        False для DRAFT и AWAITING_APPROVAL — запуск без подтверждения.
        """
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

            # Если документ не в DRAFT — сбрасываем в DRAFT перед созданием job.
            if document.status != DocumentStatusVO.DRAFT:
                document = await self._uow.documents.update_status(
                    document, DocumentStatusVO.DRAFT
                )

            job = await self._uow.jobs.create_for_document(
                document,
                status=AnalysisJobStatusVO.PENDING,
                idempotency_key=idempotency_key,
            )

            # FIX-P0-DISPATCH: устанавливаем current_analysis_job_id в той же
            # транзакции, чтобы колонка никогда не оставалась NULL после создания job.
            document.current_analysis_job_id = job.id
            await self._uow.session.flush()

            await self._uow.commit()
        return job

    async def dispatch_job(self, job: "AnalysisJob") -> None:
        """Отправить job в очередь Celery.

        FIX-P0-DISPATCH: метод, который ранее отсутствовал и вызывался
        роутером (приводило к AttributeError → каждый job сразу FAILED).

        Алгоритм:
          1. Собрать список source_ids из job.sources (eager-loaded в репозитории).
          2. Построить chord: N задач process_source_for_analysis_job
             + callback finalize_analysis_job.
          3. Отправить chord в Celery.
          4. Вызвать mark_dispatched() с полученным task_id (id chord-группы).

        Если Celery недоступен — chord.delay() / chord.apply_async() бросит
        исключение; роутер перехватывает его и вызывает
        mark_job_queue_unavailable().

        Импорты Celery-задач выполняются лениво (внутри метода), чтобы
        не нарушать правило «никаких инфраструктурных импортов на верхнем
        уровне модуля».
        """
        if job.status not in _DISPATCHABLE_JOB_STATUSES:
            raise InvalidDocumentStatusError(
                f"Диспатч недопустим для задачи в статусе {job.status!r}. "
                f"Допустимые статусы: {', '.join(s.value for s in _DISPATCHABLE_JOB_STATUSES)}"
            )

        # Lazy import — инфраструктурный уровень, недопустим на верхнем уровне.
        from celery import chord  # noqa: PLC0415
        from app.workers.tasks import (  # noqa: PLC0415
            finalize_analysis_job,
            process_source_for_analysis_job,
        )

        job_id_str = str(job.id)
        source_tasks = [
            process_source_for_analysis_job.si(job_id_str, str(source.id))
            for source in job.sources
        ]

        if source_tasks:
            result = chord(source_tasks)(
                finalize_analysis_job.si(job_id_str)
            )
        else:
            # Нет источников — сразу финализируем.
            result = finalize_analysis_job.delay(job_id_str)

        task_id = result.id
        await self.mark_dispatched(job, task_id)

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

            await self._uow.suggestions.reset_to_pending_by_job(
                document.current_analysis_job_id
            )
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
