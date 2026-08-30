"""Репозиторий правок.

ИСПРАВЛЕНО: list_by_analysis_job теперь принимает limit/offset, добавлен
count_by_analysis_job. Также добавлены list_ids_by_analysis_job_and_status и
list_by_analysis_job_and_status — фильтр по status теперь происходит на уровне SQL
(WHERE status = ...), а не выборкой всех строк с последующей фильтрацией в Python —
используются в SuggestionService.bulk_accept()/get_accepted_changes(), где раньше
тянулись ВСЕ правки документа ради подмножества нужного статуса.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.enums import SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion


class SuggestionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def bulk_create(self, suggestions: list[Suggestion]) -> list[Suggestion]:
        if not suggestions:
            return []
        self._session.add_all(suggestions)
        await self._session.commit()
        return suggestions

    async def get_by_id(self, suggestion_id: uuid.UUID) -> Suggestion | None:
        return await self._session.get(Suggestion, suggestion_id)

    async def list_by_analysis_job(self, analysis_job_id: uuid.UUID, limit: int, offset: int) -> list[Suggestion]:
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
            select(func.count()).select_from(Suggestion).where(Suggestion.analysis_job_id == analysis_job_id)
        )
        return result.scalar_one()

    async def list_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatus
    ) -> list[Suggestion]:
        result = await self._session.execute(
            select(Suggestion)
            .where(Suggestion.analysis_job_id == analysis_job_id, Suggestion.status == status)
            .order_by(Suggestion.created_at)
        )
        return list(result.scalars().all())

    async def list_ids_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatus
    ) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(Suggestion.id).where(Suggestion.analysis_job_id == analysis_job_id, Suggestion.status == status)
        )
        return list(result.scalars().all())

    async def update_status(self, suggestion: Suggestion, status: SuggestionStatus, decided_by: uuid.UUID) -> Suggestion | None:
        """
        Атомарный UPDATE ... WHERE status = 'pending' — защита от гонки при двойном
        accept/reject одной и той же правки параллельными запросами: если suggestion уже
        был решён другим запросом, обновление не применится, и вызывающий код
        узнаёт об этом по возвращаемому None.
        """
        stmt = (
            update(Suggestion)
            .where(Suggestion.id == suggestion.id, Suggestion.status == SuggestionStatus.PENDING)
            .values(status=status, decided_by=decided_by, decided_at=datetime.now(UTC))
            .returning(Suggestion)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        await self._session.commit()
        if updated is None:
            return None
        await self._session.refresh(updated)
        return updated

    async def bulk_update_status(
        self, suggestion_ids: list[uuid.UUID], status: SuggestionStatus, decided_by: uuid.UUID
    ) -> list[Suggestion]:
        """
        Принимает только id и делает один атомарный UPDATE ... WHERE status = 'pending' —
        правки, решённые параллельно между выборкой id и этим запросом, автоматически
        не попадут в обновление.
        """
        if not suggestion_ids:
            return []
        stmt = (
            update(Suggestion)
            .where(Suggestion.id.in_(suggestion_ids), Suggestion.status == SuggestionStatus.PENDING)
            .values(status=status, decided_by=decided_by, decided_at=datetime.now(UTC))
            .returning(Suggestion)
        )
        result = await self._session.execute(stmt)
        updated = list(result.scalars().all())
        await self._session.commit()
        return updated
