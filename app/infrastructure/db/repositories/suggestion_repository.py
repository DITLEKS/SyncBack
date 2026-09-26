"""
SQLAlchemy-адаптер для Suggestion.

Правило: НИКАКИХ session.commit() / session.rollback() здесь.
Все изменения фиксирует SqlAlchemyUnitOfWork через uow.commit().

ИЗМЕНЕНИЯ:
- Публичные сигнатуры методов принимают / возвращают domain VO
  (SuggestionStatusVO) вместо ORM-enum SuggestionStatus.
- Конвертация VO ↔ ORM-enum инкапсулирована в _to_orm / _from_orm.
- list_by_analysis_job принимает PaginationParams.
- bulk_update_status / update_status принимают ReviewDecisions или SuggestionDecision.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import ISuggestionRepository
from app.domain.value_objects import (
    PaginationParams,
    ReviewDecisions,
    SuggestionDecision,
    SuggestionStatusVO,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.suggestion import Suggestion

# ---------------------------------------------------------------------------
# Локальные конвертеры VO ↔ ORM-enum
# Импорт ORM-enum отложен, чтобы инфра-слой не «просачивался» в домен.
# ---------------------------------------------------------------------------

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
        pagination: PaginationParams,
    ) -> list[Suggestion]:
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        result = await self._session.execute(
            select(SuggestionModel)
            .where(SuggestionModel.analysis_job_id == analysis_job_id)
            .order_by(SuggestionModel.created_at)
            .limit(pagination.limit)
            .offset(pagination.offset)
        )
        return list(result.scalars().all())

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
        Caller обязан вызвать uow.commit() после.
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
        Caller обязан вызвать uow.commit() после.
        """
        from app.infrastructure.db.models.suggestion import Suggestion as SuggestionModel
        from app.infrastructure.db.models.enums import SuggestionStatus

        all_ids = [*decisions.accepted_ids, *decisions.rejected_ids]
        if not all_ids:
            return []

        # Единый UPDATE с CASE-выражением, чтобы избежать двух запросов
        # (один для accepted, один для rejected).
        from sqlalchemy import case
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
