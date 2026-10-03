"""SQLAlchemy-адаптер для AnalysisJob.

Репозиторий не трогает статус документа: методы переходов возвращают
DocumentStatusVO, который сервис применяет через uow.documents.update_status.
Фиксация транзакции — на стороне UoW.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import IAnalysisJobRepository
from app.domain.value_objects import AnalysisJobStatusVO, DocumentStatusVO, KeysetPage
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AnalysisJobStatus


def _status_to_orm(vo: AnalysisJobStatusVO):
    return AnalysisJobStatus(vo.value)


class AnalysisJobRepository(IAnalysisJobRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_id(self, job_id: uuid.UUID) -> AnalysisJob | None:
        return await self._session.get(AnalysisJob, job_id)

    async def get_by_idempotency_key(
        self, document_id: uuid.UUID, idempotency_key: str
    ) -> AnalysisJob | None:
        result = await self._session.execute(
            select(AnalysisJob).where(
                AnalysisJob.document_id == document_id,
                AnalysisJob.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_document_id(self, document_id: uuid.UUID) -> AnalysisJob | None:
        result = await self._session.execute(
            select(AnalysisJob)
            .where(
                AnalysisJob.document_id == document_id,
                AnalysisJob.status.in_((AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)),
            )
            .order_by(AnalysisJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_document(
        self,
        document_id: uuid.UUID,
        pagination,
    ) -> list[AnalysisJob]:
        stmt = (
            select(AnalysisJob)
            .where(AnalysisJob.document_id == document_id)
            .order_by(AnalysisJob.created_at.desc())
        )
        if isinstance(pagination, KeysetPage):
            if pagination.has_cursor:
                stmt = stmt.where(
                    (AnalysisJob.created_at < pagination.before_created_at)
                    | (
                        (AnalysisJob.created_at == pagination.before_created_at)
                        & (AnalysisJob.id < pagination.before_id)
                    )
                )
            stmt = stmt.limit(pagination.limit)
        else:
            stmt = stmt.limit(pagination.limit).offset(pagination.offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create_for_document(
        self,
        document: Document,
        *,
        job_id: uuid.UUID | None = None,
        status: AnalysisJobStatusVO = AnalysisJobStatusVO.PENDING,
        idempotency_key: str | None = None,
    ) -> AnalysisJob:
        """Фабричный метод: создаёт ORM-объект AnalysisJob внутри репозитория.

        CRIT-1: репозиторий НЕ мутирует document.current_analysis_job_id.
        Вызывающий сервис обязан сам выполнить:
            document.current_analysis_job_id = job.id
        или использовать uow.documents.set_current_job(document, job.id).
        Это соблюдает SRP: jobs-репозиторий не знает про структуру Document.

        H-NEW-1: session.refresh(job) удалён — flush() достаточно.
        Commit — ответственность вызывающего UoW.
        """
        job = AnalysisJob(
            id=job_id or uuid.uuid4(),
            document_id=document.id,
            status=_status_to_orm(status),
            idempotency_key=idempotency_key,
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def mark_dispatched(
        self, job: AnalysisJob
    ) -> tuple[AnalysisJob, DocumentStatusVO | None]:
        """PENDING → PROCESSING до отправки в очередь.

        Статусы выставляются заранее, чтобы быстрый воркер не был перезаписан
        более поздней записью «в обработке». Если задача уже не PENDING —
        ничего не меняем.
        """
        if job.status.value != AnalysisJobStatusVO.PENDING.value:
            return job, None
        job = await self.update_status(job, AnalysisJobStatusVO.PROCESSING)
        return job, DocumentStatusVO.IN_PROGRESS

    async def set_celery_task_id(self, job: AnalysisJob, task_id: str) -> AnalysisJob:
        job.celery_task_id = task_id
        await self._session.flush()
        return job

    async def mark_failed_queue_unavailable(
        self,
        job: AnalysisJob,
        message: str | None,
    ) -> tuple[AnalysisJob, DocumentStatusVO]:
        """CRIT-B / HIGH-B: делегирует update_status(), не мутирует document.

        Возвращает (job, DocumentStatusVO.DRAFT) — вызывающий код обновляет документ.
        """
        job = await self.update_status(
            job,
            AnalysisJobStatusVO.FAILED,
            error_code="QUEUE_UNAVAILABLE",
            error_message=message,
        )
        return job, DocumentStatusVO.DRAFT

    async def cancel(
        self,
        job: AnalysisJob,
    ) -> tuple[AnalysisJob, DocumentStatusVO]:
        """CRIT-B / HIGH-B: делегирует update_status(), не мутирует document.

        Возвращает (job, DocumentStatusVO.DRAFT) — вызывающий код обновляет документ.
        """
        job = await self.update_status(
            job,
            AnalysisJobStatusVO.CANCELLED,
            error_code="ANALYSIS_CANCELLED",
            error_message="Анализ отменён",
        )
        return job, DocumentStatusVO.DRAFT

    async def mark_processing_if_active(self, job_id: uuid.UUID) -> bool:
        """Atomic CAS: PENDING/PROCESSING → PROCESSING.

        M-B: использует RETURNING вместо rowcount для надёжности на asyncpg.
        Возвращает True если строка была обновлена.
        """
        result = await self._session.execute(
            update(AnalysisJob)
            .where(
                AnalysisJob.id == job_id,
                AnalysisJob.status.in_((AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)),
            )
            .values(
                status=AnalysisJobStatus.PROCESSING,
                started_at=func.coalesce(AnalysisJob.started_at, datetime.now(UTC)),
            )
            .returning(AnalysisJob.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None

    async def update_status(
        self,
        job: AnalysisJob,
        status: AnalysisJobStatusVO,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AnalysisJob:
        """Сменить статус задачи, проставив started_at/finished_at по переходу."""
        _terminal = {
            AnalysisJobStatus.SUCCESS,
            AnalysisJobStatus.PARTIAL_SUCCESS,
            AnalysisJobStatus.FAILED,
            AnalysisJobStatus.CANCELLED,
        }
        orm_status = _status_to_orm(status)
        job.status = orm_status
        job.error_code = error_code
        job.error_message = error_message
        now = datetime.now(UTC)
        if orm_status == AnalysisJobStatus.PROCESSING and job.started_at is None:
            job.started_at = now
        if orm_status in _terminal:
            job.finished_at = now
        await self._session.flush()
        return job
