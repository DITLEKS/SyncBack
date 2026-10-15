"""Репозиторий состояния обработки источников (P0-2). commit делает UoW."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.analysis_job_source import (
    SOURCE_RUN_COMPLETED,
    SOURCE_RUN_PENDING,
    SOURCE_RUN_PROCESSING,
    AnalysisJobSource,
)


class AnalysisJobSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure(self, job_id: uuid.UUID, source_id: uuid.UUID) -> AnalysisJobSource:
        """Вернуть запись (job_id, source_id), создав её при отсутствии.

        Вставка идёт в savepoint: проигравшая гонку получает IntegrityError,
        откатывает только savepoint и читает уже созданную запись.
        """
        existing = await self._get(job_id, source_id)
        if existing is not None:
            return existing
        try:
            async with self._session.begin_nested():
                self._session.add(AnalysisJobSource(job_id=job_id, source_id=source_id))
        except IntegrityError:
            pass
        row = await self._get(job_id, source_id)
        if row is None:
            raise RuntimeError("analysis_job_sources: запись не создана")
        return row

    async def claim(self, job_id: uuid.UUID, source_id: uuid.UUID, lease_seconds: int) -> bool:
        """Атомарно захватить источник перед вызовом LLM (P0-2b).

        True — источник был pending либо в processing с просроченным lease и
        теперь принадлежит этому воркеру; False — его держит другой воркер или
        он уже completed. Конкурентный UPDATE ждёт блокировку строки и после
        коммита первого перепроверяет условие.
        """
        now = datetime.now(UTC)
        expired_before = now - timedelta(seconds=lease_seconds)
        result = await self._session.execute(
            update(AnalysisJobSource)
            .where(
                AnalysisJobSource.job_id == job_id,
                AnalysisJobSource.source_id == source_id,
                or_(
                    AnalysisJobSource.status == SOURCE_RUN_PENDING,
                    and_(
                        AnalysisJobSource.status == SOURCE_RUN_PROCESSING,
                        AnalysisJobSource.locked_at < expired_before,
                    ),
                ),
            )
            .values(status=SOURCE_RUN_PROCESSING, locked_at=now)
        )
        return result.rowcount == 1  # type: ignore[attr-defined]

    async def release(self, job_id: uuid.UUID, source_id: uuid.UUID) -> None:
        """Вернуть источник в pending после сбоя или отмены, чтобы повтор не ждал lease."""
        await self._session.execute(
            update(AnalysisJobSource)
            .where(
                AnalysisJobSource.job_id == job_id,
                AnalysisJobSource.source_id == source_id,
                AnalysisJobSource.status == SOURCE_RUN_PROCESSING,
            )
            .values(status=SOURCE_RUN_PENDING, locked_at=None)
        )

    async def mark_completed(
        self, job_id: uuid.UUID, source_id: uuid.UUID, suggestions_count: int
    ) -> bool:
        """Атомарно пометить источник завершённым.

        True — этот вызов был первым и должен записать правки в той же
        транзакции; False — источник уже завершён (дубль доставки
        или конкурентный воркер). Конкурентный UPDATE ждёт блокировку
        строки и после коммита первого перепроверяет условие.
        """
        result = await self._session.execute(
            update(AnalysisJobSource)
            .where(
                AnalysisJobSource.job_id == job_id,
                AnalysisJobSource.source_id == source_id,
                AnalysisJobSource.status != SOURCE_RUN_COMPLETED,
            )
            .values(
                status=SOURCE_RUN_COMPLETED,
                suggestions_count=suggestions_count,
                finished_at=datetime.now(UTC),
                locked_at=None,
            )
        )
        return result.rowcount == 1  # type: ignore[attr-defined]

    async def get_completed_count(self, job_id: uuid.UUID, source_id: uuid.UUID) -> int | None:
        row = await self._get(job_id, source_id)
        if row is None or row.status != SOURCE_RUN_COMPLETED:
            return None
        return row.suggestions_count

    async def _get(self, job_id: uuid.UUID, source_id: uuid.UUID) -> AnalysisJobSource | None:
        stmt = (
            select(AnalysisJobSource)
            .where(
                AnalysisJobSource.job_id == job_id,
                AnalysisJobSource.source_id == source_id,
            )
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
