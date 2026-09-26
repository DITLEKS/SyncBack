"""
SQLAlchemy-адаптер для Suggestion.

Правило: НИКАКИХ session.commit() / session.rollback() здесь.
Все изменения фиксирует SqlAlchemyUnitOfWork через uow.commit().

ИЗМЕНЕНИЯ:
- Публичные сигнатуры методов принимают / возвращают domain VO
  (SuggestionStatusVO) вместо ORM-enum SuggestionStatus.
- Конвертация VO ↔ ORM-enum инкапсулирована в _to_orm / _from_orm.
- list_by_analysis_job переведён на KeysetPage: O(log N) вместо O(N) на OFFSET.
- bulk_update_status / update_status принимают ReviewDecisions или SuggestionDecision.
- list_with_total: один SELECT с COUNT(*) OVER() вместо двух запросов (H-3).
- bulk_accept_all: один UPDATE без предварительной загрузки UUID в память (H-4).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import ISuggestionRepository
from app.domain.value_objects import (
    KeysetPage,
    PaginationParams,
    ReviewDecisions,
    SuggestionDecision,
    SuggestionStatusVO,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.suggestion import Suggestion


def _status_to_orm(vo: SuggestionStatusVO):
    from app.infrastructure.db.models.enums import SuggestionStatus
    return SuggestionStatus(vo.value)


def _status_from_orm(orm_val) -> SuggestionStatusVO:
    return SuggestionStatusVO(orm_val.value)


class SuggestionRepository(ISuggestionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _flush_and_refresh(self, obj) -> None:
        await self._session.flush()
        await self._session.refresh(obj)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_id(self, suggestion_id: uuid.UUID) -> Suggestion | None:
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        return await self._session.get(SuggestionModel, suggestion_id)

    async def list_by_analysis_job(
        self,
        analysis_job_id: uuid.UUID,
        pagination: KeysetPage | PaginationParams,
    ) -> list[Suggestion]:
        """Постраничный список правок для задачи анализа.

        KeysetPage (предпочтительно): использует индекс
        ix_suggestions_job_created_at_id → O(log N).
        PaginationParams (legacy): OFFSET-запрос, оставлен для совместимости.
        """
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel

        q = (
            select(SuggestionModel)
            .where(SuggestionModel.analysis_job_id == analysis_job_id)
            .order_by(SuggestionModel.created_at, SuggestionModel.id)
            .limit(pagination.limit)
        )

        if isinstance(pagination, KeysetPage) and pagination.has_cursor:
            q = q.where(
                tuple_(SuggestionModel.created_at, SuggestionModel.id)
                > tuple_(pagination.before_created_at, pagination.before_id)
            )
        elif isinstance(pagination, PaginationParams):
            q = q.offset(pagination.offset)

        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def list_with_total(
        self,
        analysis_job_id: uuid.UUID,
        pagination: PaginationParams,
    ) -> tuple[list[Suggestion], int]:
        """Один SELECT с оконной функцией COUNT(*) OVER() вместо двух запросов.

        Возвращает (items, total) за один round-trip к БД.
        Использует индекс ix_suggestions_job_created_at_id.
        """
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel

        total_col = func.count().over().label("total_count")
        q = (
            select(SuggestionModel, total_col)
            .where(SuggestionModel.analysis_job_id == analysis_job_id)
            .order_by(SuggestionModel.created_at, SuggestionModel.id)
            .limit(pagination.limit)
            .offset(pagination.offset)
        )
        rows = (await self._session.execute(q)).all()
        if not rows:
            return [], 0
        items = [row[0] for row in rows]
        total = rows[0][1]
        return items, total

    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int:
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        result = await self._session.execute(
            select(func.count())
            .select_from(SuggestionModel)
            .where(SuggestionModel.analysis_job_id == analysis_job_id)
        )
        return result.scalar_one()

    async def count_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> int:
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        result = await self._session.execute(
            select(func.count())
            .select_from(SuggestionModel)
            .where(
                SuggestionModel.analysis_job_id == analysis_job_id,
                SuggestionModel.status == _status_to_orm(status),
            )
        )
        return result.scalar_one()

    async def list_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> list[Suggestion]:
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        result = await self._session.execute(
            select(SuggestionModel)
            .where(
                SuggestionModel.analysis_job_id == analysis_job_id,
                SuggestionModel.status == _status_to_orm(status),
            )
            .order_by(SuggestionModel.created_at)
        )
        return list(result.scalars().all())

    async def list_ids_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> list[uuid.UUID]:
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        result = await self._session.execute(
            select(SuggestionModel.id).where(
                SuggestionModel.analysis_job_id == analysis_job_id,
                SuggestionModel.status == _status_to_orm(status),
            )
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def bulk_create(
        self, suggestions: list[Suggestion]
    ) -> list[Suggestion]:
        if not suggestions:
            return []
        self._session.add_all(suggestions)
        await self._session.flush()
        return suggestions

    async def update_status(
        self,
        suggestion: Suggestion,
        decision: SuggestionDecision,
    ) -> Suggestion | None:
        """
        Атомарный UPDATE ... WHERE status = 'pending'.
        Защита от гонки: если правка уже решена — возвращает None.
        """
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        from app.infrastructure.db.models.enums import SuggestionStatus
        stmt = (
            update(SuggestionModel)
            .where(
                SuggestionModel.id == suggestion.id,
                SuggestionModel.status == SuggestionStatus.PENDING,
            )
            .values(
                status=_status_to_orm(decision.status),
                decided_by=decision.decided_by,
                decided_at=datetime.now(UTC),
            )
            .returning(SuggestionModel)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        if updated is None:
            return None
        await self._session.flush()
        return updated

    async def bulk_update_status(
        self,
        decisions: ReviewDecisions,
    ) -> list[Suggestion]:
        """
        Один UPDATE ... WHERE id IN (...) AND status = 'pending'.
        Правки, решённые параллельно, автоматически пропускаются.
        """
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        from app.infrastructure.db.models.enums import SuggestionStatus

        all_ids = [*decisions.accepted_ids, *decisions.rejected_ids]
        if not all_ids:
            return []

        stmt = (
            update(SuggestionModel)
            .where(
                SuggestionModel.id.in_(all_ids),
                SuggestionModel.analysis_job_id == decisions.analysis_job_id,
                SuggestionModel.status == SuggestionStatus.PENDING,
            )
            .values(
                status=case(
                    {
                        True: SuggestionStatus.ACCEPTED,
                        False: SuggestionStatus.REJECTED,
                    },
                    value=SuggestionModel.id.in_(decisions.accepted_ids),
                ),
                decided_by=decisions.decided_by,
                decided_at=datetime.now(UTC),
            )
            .returning(SuggestionModel)
        )
        result = await self._session.execute(stmt)
        updated = list(result.scalars().all())
        await self._session.flush()
        return updated

    async def bulk_accept_all(
        self,
        analysis_job_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[Suggestion]:
        """Принять все PENDING-правки одним UPDATE без загрузки UUID в память (H-4).

        UPDATE ... WHERE analysis_job_id=X AND status='pending' RETURNING *
        вместо list_ids → bulk_update_status(IN (uuid, uuid, ...)).
        """
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        from app.infrastructure.db.models.enums import SuggestionStatus

        stmt = (
            update(SuggestionModel)
            .where(
                SuggestionModel.analysis_job_id == analysis_job_id,
                SuggestionModel.status == SuggestionStatus.PENDING,
            )
            .values(
                status=SuggestionStatus.ACCEPTED,
                decided_by=user_id,
                decided_at=datetime.now(UTC),
            )
            .returning(SuggestionModel)
        )
        result = await self._session.execute(stmt)
        updated = list(result.scalars().all())
        await self._session.flush()
        return updated
