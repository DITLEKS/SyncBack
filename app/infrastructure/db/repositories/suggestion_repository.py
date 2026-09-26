"""
SQLAlchemy-адаптер для Suggestion.

Правило: НИКАКИХ session.commit() / session.rollback() здесь.
Все изменения фиксирует SqlAlchemyUnitOfWork через uow.commit().

ИСПРАВЛЕНО:
- list_by_analysis_job принимает limit/offset (пагинация на уровне SQL).
- bulk_update_status — один UPDATE ... WHERE id IN (...) AND status = 'pending'
  вместо N отдельных обновлений (O(N) → O(1) round-trips).
- Все commit() удалены; flush() используется там, где нужен RETURNING.
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import ISuggestionRepository
from app.infrastructure.db.models.enums import SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion


class SuggestionRepository(ISuggestionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(self, suggestions: list[Suggestion]) -> list[Suggestion]:
        if not suggestions:
            return []
        self._session.add_all(suggestions)
        await self._session.flush()
        return suggestions

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

    async def list_ids_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatus
    ) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(Suggestion.id).where(
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == status,
            )
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        suggestion: Suggestion,
        status: SuggestionStatus,
        decided_by: uuid.UUID,
    ) -> Suggestion | None:
        """
        Атомарный UPDATE ... WHERE status = 'pending'.
        Защита от гонки: если правка уже решена — возвращает None.
        Caller обязан вызвать uow.commit() после.
        """
        stmt = (
            update(Suggestion)
            .where(
                Suggestion.id == suggestion.id,
                Suggestion.status == SuggestionStatus.PENDING,
            )
            .values(status=status, decided_by=decided_by, decided_at=datetime.now(UTC))
            .returning(Suggestion)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        if updated is None:
            return None
        await self._session.flush()
        return updated

    async def bulk_update_status(
        self,
        analysis_job_id: uuid.UUID,
        suggestion_ids: list[uuid.UUID],
        status: SuggestionStatus,
        decided_by: uuid.UUID,
    ) -> list[Suggestion]:
        """
        Один UPDATE ... WHERE id IN (...) AND status = 'pending'.
        Правки, решённые параллельно, автоматически не попадают в обновление.
        Caller обязан вызвать uow.commit() после.
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
            .values(status=status, decided_by=decided_by, decided_at=datetime.now(UTC))
            .returning(Suggestion)
        )
        result = await self._session.execute(stmt)
        updated = list(result.scalars().all())
        await self._session.flush()
        return updated
