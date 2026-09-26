"""Репозиторий правок."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import SuggestionStatus
from app.domain.ports.suggestion_port import SuggestionCounts
from app.infrastructure.db.models.suggestion import Suggestion


class SuggestionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def bulk_create(self, suggestions: list[Suggestion]) -> list[Suggestion]:
        if not suggestions:
            return []
        self._session.add_all(suggestions)
        await self._session.flush()
        return suggestions

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_id(self, suggestion_id: uuid.UUID) -> Suggestion | None:
        return await self._session.get(Suggestion, suggestion_id)

    async def list_by_analysis_job(
        self, analysis_job_id: uuid.UUID, limit: int, offset: int
    ) -> list[Suggestion]:
        result = await self._session.execute(
            select(Suggestion)
            .where(Suggestion.analysis_job_id == analysis_job_id)
            .order_by(Suggestion.created_at)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Suggestion)
            .where(Suggestion.analysis_job_id == analysis_job_id)
        )
        return result.scalar_one()

    async def count_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatus
    ) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Suggestion)
            .where(
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == status,
            )
        )
        return result.scalar_one()

    async def count_by_analysis_job_stats(
        self, analysis_job_id: uuid.UUID
    ) -> SuggestionCounts:
        """Возвращает (accepted, rejected, pending) одним запросом.

        Использует COUNT(*) FILTER (WHERE status = ...) вместо трёх
        отдельных COUNT-запросов — 1 RTT вместо 3.
        """
        result = await self._session.execute(
            select(
                func.count(
                    case((Suggestion.status == SuggestionStatus.ACCEPTED, 1))
                ).label("accepted"),
                func.count(
                    case((Suggestion.status == SuggestionStatus.REJECTED, 1))
                ).label("rejected"),
                func.count(
                    case((Suggestion.status == SuggestionStatus.PENDING, 1))
                ).label("pending"),
            ).where(Suggestion.analysis_job_id == analysis_job_id)
        )
        row = result.one()
        return SuggestionCounts(
            accepted=row.accepted,
            rejected=row.rejected,
            pending=row.pending,
        )

    async def list_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatus
    ) -> list[Suggestion]:
        result = await self._session.execute(
            select(Suggestion)
            .where(
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == status,
            )
            .order_by(Suggestion.created_at)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def update_status(
        self,
        suggestion: Suggestion,
        status: SuggestionStatus,
        decided_by: uuid.UUID,
    ) -> Suggestion | None:
        """Атомарный UPDATE WHERE status = 'pending' — защита от гонки при
        параллельных accept/reject одной правки.

        UPDATE...RETURNING уже содержит актуальное состояние — flush/refresh
        не нужны.
        """
        stmt = (
            update(Suggestion)
            .where(
                Suggestion.id == suggestion.id,
                Suggestion.status == SuggestionStatus.PENDING,
            )
            .values(
                status=status,
                decided_by=decided_by,
                decided_at=datetime.now(UTC),
            )
            .returning(Suggestion)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def bulk_update_status(
        self,
        suggestion_ids: list[uuid.UUID],
        analysis_job_id: uuid.UUID,
        status: SuggestionStatus,
        decided_by: uuid.UUID,
    ) -> list[Suggestion]:
        """UPDATE WHERE id IN (...) AND analysis_job_id = ... AND status = 'pending'.

        Параметр analysis_job_id обязателен — предотвращает изменение правок
        из чужого документа при передаче произвольных UUID в теле запроса.
        Правки, решённые параллельно, автоматически пропускаются.
        """
        if not suggestion_ids:
            return []
        stmt = (
            update(Suggestion)
            .where(
                Suggestion.id.in_(suggestion_ids),
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == SuggestionStatus.PENDING,
            )
            .values(
                status=status,
                decided_by=decided_by,
                decided_at=datetime.now(UTC),
            )
            .returning(Suggestion)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def bulk_update_all_pending(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatus,
        decided_by: uuid.UUID,
    ) -> list[Suggestion]:
        """Принимает/отклоняет все pending-правки job одним UPDATE.

        Используется в bulk_accept вместо двух запросов:
          1) SELECT id WHERE status='pending'
          2) UPDATE WHERE id IN (...)
        Теперь это один UPDATE WHERE analysis_job_id=... AND status='pending'
        RETURNING *, что экономит один RTT и промежуточную аллокацию UUID-списка.
        """
        stmt = (
            update(Suggestion)
            .where(
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == SuggestionStatus.PENDING,
            )
            .values(
                status=status,
                decided_by=decided_by,
                decided_at=datetime.now(UTC),
            )
            .returning(Suggestion)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
