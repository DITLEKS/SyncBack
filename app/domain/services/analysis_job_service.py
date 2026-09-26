"""
Бизнес-логика управления задачами анализа.

H2.2: сервис принимает AnalysisJobPort / DocumentPort вместо конкретных репозиториев.
      Все enum-импорты перенесены в app.domain.enums.
      sqlalchemy.exc.IntegrityError перехватывается в infrastructure-слое;
      сервис получает только доменные исключения.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.domain.enums import AnalysisJobStatus, DocumentStatus
from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.domain.ports.analysis_job_port import AnalysisJobPort
from app.domain.ports.document_port import DocumentPort

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.document import Document

# Статусы документа, из которых разрешён запуск анализа:
#   DRAFT              — первичный / повторный запуск (переходы №2, №7→2)
#   AWAITING_APPROVAL  — повторный запуск после изменений (переход №9)
#   READY              — повторный анализ с force=True (#9 роутер)
_ANALYSIS_ALLOWED_STATUSES = (
    DocumentStatus.DRAFT,
    DocumentStatus.AWAITING_APPROVAL,
    DocumentStatus.READY,
)


class AnalysisJobService:
    def __init__(
        self,
        analysis_job_repository: AnalysisJobPort,
        document_repository: DocumentPort,
    ):
        self._jobs = analysis_job_repository
        self._documents = document_repository

    # ------------------------------------------------------------------
    # Idempotency helpers (P0-7)
    # ------------------------------------------------------------------

    async def find_job_by_idempotency_key(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        idempotency_key: str,
    ) -> "AnalysisJob | None":
        """Найти существующий job по ключу идемпотентности.

        Проверяет принадлежность документа проекту перед поиском.
        Возвращает None, если документ не найден или job с таким ключом
        не существует (роутер должен создать новый job в этом случае).
        """
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            return None
        return await self._jobs.get_by_idempotency_key(document_id, idempotency_key)

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    async def get_document_for_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> "Document":
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
    ) -> "AnalysisJob":
        """Создать задачу анализа.

        Разрешённые исходные статусы документа (таблица переходов):
          • DRAFT             → переход №2 (первичный/ручной запуск)
          • AWAITING_APPROVAL → переход №9 (повторный запуск)
          • READY             → разрешен только с force=True (#9),
                               роутер проверяет это до вызова create_job

        При повторном запуске из AWAITING_APPROVAL/READY документ сбрасывается
        в DRAFT (пайплайн всегда стартует из DRAFT → IN_PROGRESS).

        Примечание: IntegrityError от SQLAlchemy теперь перехватывается
        в AnalysisJobRepository.create_for_document и перебрасывается как
        AnalysisAlreadyRunningError, поэтому здесь импортировать sqlalchemy
        не нужно.
        """
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )

        # Idempotency-check (P0-7)
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

        from app.infrastructure.db.models.analysis_job import AnalysisJob as AnalysisJobModel
        job = AnalysisJobModel(
            id=uuid.uuid4(),
            document_id=document.id,
            status=AnalysisJobStatus.PENDING,
            idempotency_key=idempotency_key,
        )
        try:
            return await self._jobs.create_for_document(job, document)
        except AnalysisAlreadyRunningError:
            # Re-raised from repository after catching IntegrityError
            if previous_status != DocumentStatus.DRAFT:
                await self._documents.update_status(document, previous_status)
            raise
        except Exception:
            if previous_status != DocumentStatus.DRAFT:
                await self._documents.update_status(document, previous_status)
            raise

    async def mark_dispatched(
        self, job: "AnalysisJob", task_id: str
    ) -> "AnalysisJob":
        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
        return await self._jobs.mark_dispatched(job, document, task_id)

    async def mark_job_queue_unavailable(
        self, job: "AnalysisJob", error_message: str | None = None
    ) -> "AnalysisJob":
        """Очередь недоступна — задача не поставлена, документ остаётся в DRAFT (переход №3)."""
        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise DocumentNotFoundError(f"Документ {job.document_id} не найден")
        return await self._jobs.mark_failed_queue_unavailable(job, document, error_message)

    async def cancel_job(
        self, project_id: uuid.UUID, document_id: uuid.UUID, job_id: uuid.UUID
    ) -> "AnalysisJob":
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
    ) -> "AnalysisJob":
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
