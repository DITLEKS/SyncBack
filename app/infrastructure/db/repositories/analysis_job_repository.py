"""
SQLAlchemy-адаптер для AnalysisJob.

Правило: НИКАКИХ session.commit() / session.rollback() здесь.
Все изменения фиксирует SqlAlchemyUnitOfWork через uow.commit().

ИЗМЕНЕНИЯ:
- Публичные методы принимают / возвращают AnalysisJobStatusVO вместо ORM-enum.
- Конвертация инкапсулирована в _status_to_orm / _status_from_orm.
- Импорты ORM-моделей отложены (TYPE_CHECKING / локальные) — домен не зависит от инфры.
- H-2: create_for_document принимает параметры, а не готовый ORM-инстанс.
- HIGH-3: mark_dispatched, mark_failed_queue_unavailable, cancel — прямые мутации
  document.status заменены на _set_doc_status() через _status_to_orm(DocumentStatusVO),
  убран raw _doc_status(str) helper.
- CRIT-A/B: AnalysisJobRepository больше НЕ мутирует document.status напрямую.
  mark_dispatched / mark_failed_queue_unavailable / cancel принимают опциональный
  document и возвращают DocumentStatusVO, которую вызывающий код применяет через
  uow.documents.update_status(). Это восстанавливает инвариант одного агрегата.
- HIGH-B: mark_failed_queue_unavailable и cancel делегируют логику update_status().
- M-B: mark_processing_if_active → добавлен RETURNING для надёжного rowcount.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import IAnalysisJobRepository
from app.domain.value_objects import AnalysisJobStatusVO, DocumentStatusVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.document import Document


def _status_to_orm(vo: AnalysisJobStatusVO):
    from app.infrastructure.db.models.enums import AnalysisJobStatus
    return AnalysisJobStatus(vo.value)


class AnalysisJobRepository(IAnalysisJobRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_id(self, job_id: uuid.UUID) -> "AnalysisJob | None":
        from app.infrastructure.db.models.analysis_job import AnalysisJob as M
        return await self._session.get(M, job_id)

    async def get_by_idempotency_key(
        self, document_id: uuid.UUID, idempotency_key: str
    ) -> "AnalysisJob | None":
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
    ) -> "AnalysisJob | None":
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
        document: "Document",
        *,
        job_id: uuid.UUID | None = None,
        status: AnalysisJobStatusVO = AnalysisJobStatusVO.PENDING,
        idempotency_key: str | None = None,
    ) -> "AnalysisJob":
        """Фабричный метод: создаёт ORM-объект AnalysisJob внутри репозитория.

        CRIT-1/H-2: сигнатура синхронизирована с IAnalysisJobRepository.create_for_document.
        Сервис передаёт только параметры — репозиторий сам строит ORM-инстанс.
        Commit — ответственность вызывающего UoW.
        """
        from app.infrastructure.db.models.analysis_job import AnalysisJob as M
        job = M(
            id=job_id or uuid.uuid4(),
            document_id=document.id,
            status=_status_to_orm(status),
            idempotency_key=idempotency_key,
        )
        self._session.add(job)
        document.current_analysis_job_id = job.id
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def mark_dispatched(
        self,
        job: "AnalysisJob",
        task_id: str,
    ) -> "tuple[AnalysisJob, DocumentStatusVO | None]":
        """Пометить задачу как отправленную в Celery.

        CRIT-A: больше не мутирует document напрямую.
        Возвращает (job, new_doc_status | None) — вызывающий код применяет
        изменение документа через uow.documents.update_status().
        """
        from app.infrastructure.db.models.enums import AnalysisJobStatus
        await self._session.refresh(job)
        job.celery_task_id = task_id
        new_doc_status: DocumentStatusVO | None = None
        if job.status in (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING):
            new_doc_status = DocumentStatusVO.IN_PROGRESS
        await self._session.flush()
        await self._session.refresh(job)
        return job, new_doc_status

    async def mark_failed_queue_unavailable(
        self,
        job: "AnalysisJob",
        message: str | None,
    ) -> "tuple[AnalysisJob, DocumentStatusVO]":
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
        job: "AnalysisJob",
    ) -> "tuple[AnalysisJob, DocumentStatusVO]":
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
            .returning(M.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None

    async def update_status(
        self,
        job: "AnalysisJob",
        status: AnalysisJobStatusVO,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> "AnalysisJob":
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
