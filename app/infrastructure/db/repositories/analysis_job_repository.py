"""
SQLAlchemy-адаптер для AnalysisJob.

Правило: НИКАКИХ session.commit() / session.rollback() здесь.
Все изменения фиксирует SqlAlchemyUnitOfWork через uow.commit().

ИЗМЕНЕНИЯ:
- Публичные методы принимают / возвращают AnalysisJobStatusVO вместо ORM-enum.
- Конвертация инкапсулирована в _status_to_orm / _status_from_orm.
- Импорты ORM-моделей отложены (TYPE_CHECKING / локальные) — домен не зависит от инфры.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import IAnalysisJobRepository
from app.domain.value_objects import AnalysisJobStatusVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.document import Document


def _status_to_orm(vo: AnalysisJobStatusVO):
    from app.infrastructure.db.models.enums import AnalysisJobStatus
    return AnalysisJobStatus(vo.value)


def _doc_status(name: str):
    """Хелпер: получает DocumentStatus ORM-enum по имени."""
    from app.infrastructure.db.models.enums import DocumentStatus
    return DocumentStatus[name]


class AnalysisJobRepository(IAnalysisJobRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_id(self, job_id: uuid.UUID) -> AnalysisJob | None:
        from app.infrastructure.db.models.analysis_job import AnalysisJob as M
        return await self._session.get(M, job_id)

    async def get_by_idempotency_key(
        self, document_id: uuid.UUID, idempotency_key: str
    ) -> AnalysisJob | None:
        from app.infrastructure.db.models.analysis_job import AnalysisJob as M
        result = await self._session.execute(
            select(M).where(
                M.document_id == document_id,
                M.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_document_id(
        self, document_id: uuid.UUID
    ) -> AnalysisJob | None:
        from app.infrastructure.db.models.analysis_job import AnalysisJob as M
        from app.infrastructure.db.models.enums import AnalysisJobStatus
        result = await self._session.execute(
            select(M)
            .where(
                M.document_id == document_id,
                M.status.in_(
                    (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)
                ),
            )
            .order_by(M.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create_for_document(
        self,
        job: AnalysisJob,
        document: Document,
    ) -> AnalysisJob:
        """Добавляет job в сессию и связывает с документом.
        Commit — ответственность вызывающего UoW.
        """
        self._session.add(job)
        document.current_analysis_job_id = job.id
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def mark_dispatched(
        self,
        job: AnalysisJob,
        document: Document,
        task_id: str,
    ) -> AnalysisJob:
        from app.infrastructure.db.models.enums import AnalysisJobStatus
        await self._session.refresh(job)
        job.celery_task_id = task_id
        if (
            job.status in (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)
            and document.current_analysis_job_id == job.id
        ):
            document.status = _doc_status("IN_PROGRESS")
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def mark_failed_queue_unavailable(
        self,
        job: AnalysisJob,
        document: Document,
        message: str | None,
    ) -> AnalysisJob:
        from app.infrastructure.db.models.enums import AnalysisJobStatus
        job.status = AnalysisJobStatus.FAILED
        job.error_code = "QUEUE_UNAVAILABLE"
        job.error_message = message
        job.finished_at = datetime.now(UTC)
        if document.current_analysis_job_id == job.id:
            document.status = _doc_status("DRAFT")
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def cancel(
        self,
        job: AnalysisJob,
        document: Document,
    ) -> AnalysisJob:
        from app.infrastructure.db.models.enums import AnalysisJobStatus
        job.status = AnalysisJobStatus.CANCELLED
        job.error_code = "ANALYSIS_CANCELLED"
        job.error_message = "Анализ отменён"
        job.finished_at = datetime.now(UTC)
        if document.current_analysis_job_id == job.id:
            document.status = _doc_status("DRAFT")
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def mark_processing_if_active(self, job_id: uuid.UUID) -> bool:
        """Atomic CAS: PENDING/PROCESSING → PROCESSING. Возвращает True если обновлено."""
        from app.infrastructure.db.models.analysis_job import AnalysisJob as M
        from app.infrastructure.db.models.enums import AnalysisJobStatus
        result = await self._session.execute(
            update(M)
            .where(
                M.id == job_id,
                M.status.in_(
                    (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)
                ),
            )
            .values(
                status=AnalysisJobStatus.PROCESSING,
                started_at=func.coalesce(M.started_at, datetime.now(UTC)),
            )
        )
        await self._session.flush()
        return bool(result.rowcount)

    async def update_status(
        self,
        job: AnalysisJob,
        status: AnalysisJobStatusVO,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AnalysisJob:
        from app.infrastructure.db.models.enums import AnalysisJobStatus
        _terminal = {
            AnalysisJobStatus.SUCCESS,
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
        await self._session.refresh(job)
        return job
