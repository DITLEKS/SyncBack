"""
<<<<<<< HEAD
Бизнес-логика управления задачами анализа.

H2.2: сервис принимает AnalysisJobPort / DocumentPort вместо конкретных репозиториев.
"""
=======
Бизнес-логика задач анализа документов.

Архитектурные правила:
  - Сервис зависит только от IUnitOfWork — не от конкретных репозиториев.
  - Один uow.commit() на операцию (кроме компенсирующих транзакций при ошибках).
  - ORM-модели под TYPE_CHECKING — временная мера до замены на domain entities.
  - Никаких импортов из app.infrastructure.* при выполнении (НЕ TYPE_CHECKING).
"""
from __future__ import annotations

>>>>>>> origin/fix/high-priority-review-findings
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
<<<<<<< HEAD
from app.domain.ports.analysis_job_port import AnalysisJobPort
from app.domain.ports.document_port import DocumentPort
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus
=======
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import AnalysisJobStatusVO, DocumentStatusVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.document import Document
>>>>>>> origin/fix/high-priority-review-findings

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


class AnalysisJobService:
<<<<<<< HEAD
    def __init__(
        self,
        analysis_job_repository: AnalysisJobPort,
        document_repository: DocumentPort,
    ):
        self._jobs = analysis_job_repository
        self._documents = document_repository
=======
    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow
>>>>>>> origin/fix/high-priority-review-findings

    # ------------------------------------------------------------------
    # Idempotency helpers
    # ------------------------------------------------------------------

    async def find_job_by_idempotency_key(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str,
<<<<<<< HEAD
    ) -> AnalysisJob | None:
        """Найти существующий job по ключу идемпотентности.

        Проверяет принадлежность документа проекту перед поиском.
        Возвращает None, если документ не найден или job с таким ключом
        не существует (роутер должен создать новый job в этом случае).
        """
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            return None
        return await self._jobs.get_by_idempotency_key(document_id, idempotency_key)
=======
    ) -> "AnalysisJob | None":
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                return None
            return await self._uow.jobs.get_by_idempotency_key(
                document_id, idempotency_key
            )
>>>>>>> origin/fix/high-priority-review-findings

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    async def get_document_for_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID
<<<<<<< HEAD
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
=======
    ) -> "Document":
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
>>>>>>> origin/fix/high-priority-review-findings
        return document

    # ------------------------------------------------------------------
    # Core job lifecycle
    # ------------------------------------------------------------------

    async def create_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str | None = None,
<<<<<<< HEAD
    ) -> AnalysisJob:
=======
    ) -> "AnalysisJob":
>>>>>>> origin/fix/high-priority-review-findings
        """Создать задачу анализа.

        Одна транзакция:
          1. Проверка статуса документа
          2. Idempotency-check (если ключ передан)
          3. Сброс документа в DRAFT (если не DRAFT)
          4. INSERT job + UPDATE document.current_analysis_job_id
          5. commit
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
                    f"{' или '.join(_ANALYSIS_ALLOWED_STATUSES)}, "
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

            # ORM-объект AnalysisJob создаётся здесь, а не в репозитории,
            # т.к. сервис владеет id-генерацией и начальным статусом.
            from app.infrastructure.db.models.analysis_job import AnalysisJob  # noqa: PLC0415
            job = AnalysisJob(
                id=uuid.uuid4(),
                document_id=document.id,
                status=AnalysisJobStatusVO.PENDING,
                idempotency_key=idempotency_key,
            )
            try:
                job = await self._uow.jobs.create_for_document(job, document)
                await self._uow.commit()
            except IntegrityError as exc:
                await self._uow.rollback()
                raise AnalysisAlreadyRunningError(
                    "Для документа уже выполняется анализ"
                ) from exc
        return job

    async def mark_dispatched(
        self, job: "AnalysisJob", task_id: str
    ) -> "AnalysisJob":
        async with self._uow:
            document = await self._uow.documents.get_by_id(job.document_id)
            if document is None:
                raise DocumentNotFoundError(
                    f"Документ {job.document_id} не найден"
                )
            result = await self._uow.jobs.mark_dispatched(job, document, task_id)
            await self._uow.commit()
        return result

    async def mark_job_queue_unavailable(
        self, job: "AnalysisJob", error_message: str | None = None
    ) -> "AnalysisJob":
        async with self._uow:
            document = await self._uow.documents.get_by_id(job.document_id)
            if document is None:
                raise DocumentNotFoundError(
                    f"Документ {job.document_id} не найден"
                )
            result = await self._uow.jobs.mark_failed_queue_unavailable(
                job, document, error_message
            )
            await self._uow.commit()
        return result

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
            result = await self._uow.jobs.cancel(job, document)
            await self._uow.commit()
        return result

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

        Каждый документ — отдельный UoW, чтобы ошибка одного
        не откатывала остальных.
        """
        async with self._uow:
            analyzable = await self._uow.documents.list_analyzable_for_project(
                project_id
            )

        results: list[dict] = []
        for document in analyzable:
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
    # Internal
    # ------------------------------------------------------------------

    async def _get_job(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> "AnalysisJob":
        """Проверить принадлежность job → document → project."""
        job = await self._uow.jobs.get_by_id(job_id)
        if job is None or job.document_id != document_id:
            raise DocumentNotFoundError(
                f"Задача анализа {job_id} не найдена для документа {document_id}"
            )
        document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return job
