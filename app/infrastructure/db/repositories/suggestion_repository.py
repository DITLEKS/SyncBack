"""
SQLAlchemy-адаптер для Suggestion.

Правило: НИКАКИХ session.commit() / session.rollback() здесь.
Все изменения фиксирует SqlAlchemyUnitOfWork через uow.commit().

ИСПРАВЛЕНИЯ:
- bulk_update_status / update_status принимают ReviewDecisions или SuggestionDecision.
- bulk_create: session.add_all() + flush().
- list_by_analysis_job_and_status_page: постраничный вариант для стримингового
  экспорта; покрывается индексом ix_suggestions_job_status (0015).
- L-D1 (этот раунд): удалён мёртвый метод _flush_and_refresh() — он был
  определён, но нигде не вызывался. Все вызовы flush() оставлены напрямую.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Sequence

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import ISuggestionRepository
from app.domain.value_objects import ReviewDecisions, SuggestionDecision, SuggestionStatusVO

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
    # Read
    # ------------------------------------------------------------------

    async def get_by_id(
        self, suggestion_id: uuid.UUID
    ) -> "Suggestion | None":
        from app.infrastructure.db.models.suggestion import Suggestion as M
        return await self._session.get(M, suggestion_id)

    async def list_by_analysis_job(
        self,
        analysis_job_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
        status: SuggestionStatusVO | None = None,
    ) -> "list[Suggestion]":
        from app.infrastructure.db.models.suggestion import Suggestion as M
        q = select(M).where(M.analysis_job_id == analysis_job_id)
        if status is not None:
            q = q.where(M.status == _status_to_orm(status))
        q = q.order_by(M.created_at.asc()).offset(offset)
        if limit is not None:
            q = q.limit(limit)
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def list_with_total(
        self,
        analysis_job_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: SuggestionStatusVO | None = None,
    ) -> "tuple[list[Suggestion], int]":
        from app.infrastructure.db.models.suggestion import Suggestion as M
        base = select(M).where(M.analysis_job_id == analysis_job_id)
        if status is not None:
            base = base.where(M.status == _status_to_orm(status))
        count_q = select(func.count()).select_from(base.subquery())
        items_q = base.order_by(M.created_at.asc()).limit(limit).offset(offset)
        rows = (await self._session.execute(
            select(M, func.count().over().label("total"))
            .where(M.analysis_job_id == analysis_job_id)
            .where(*([] if status is None else [M.status == _status_to_orm(status)]))
            .order_by(M.created_at.asc())
            .limit(limit).offset(offset)
        )).all()
        if not rows:
            total_result = await self._session.execute(count_q)
            return [], total_result.scalar_one()
        items = [r[0] for r in rows]
        total = rows[0][1]
        return items, total

    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int:
        from app.infrastructure.db.models.suggestion import Suggestion as M
        result = await self._session.execute(
            select(func.count()).select_from(M).where(
                M.analysis_job_id == analysis_job_id
            )
        )
        return result.scalar_one()

    async def count_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> int:
        from app.infrastructure.db.models.suggestion import Suggestion as M
        result = await self._session.execute(
            select(func.count()).select_from(M).where(
                M.analysis_job_id == analysis_job_id,
                M.status == _status_to_orm(status),
            )
        )
        return result.scalar_one()

    async def list_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> "list[Suggestion]":
        from app.infrastructure.db.models.suggestion import Suggestion as M
        result = await self._session.execute(
            select(M).where(
                M.analysis_job_id == analysis_job_id,
                M.status == _status_to_orm(status),
            ).order_by(M.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_by_analysis_job_and_status_page(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
        limit: int,
        offset: int = 0,
    ) -> "list[Suggestion]":
        """
        Постраничный вариант list_by_analysis_job_and_status для стримингового
        экспорта. Покрывается индексом ix_suggestions_job_status (миграция 0015).
        """
        from app.infrastructure.db.models.suggestion import Suggestion as M
        result = await self._session.execute(
            select(M).where(
                M.analysis_job_id == analysis_job_id,
                M.status == _status_to_orm(status),
            ).order_by(M.created_at.asc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def list_ids_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> list[uuid.UUID]:
        from app.infrastructure.db.models.suggestion import Suggestion as M
        result = await self._session.execute(
            select(M.id).where(
                M.analysis_job_id == analysis_job_id,
                M.status == _status_to_orm(status),
            )
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def bulk_create(
        self, suggestions: "list[Suggestion]"
    ) -> "list[Suggestion]":
        if not suggestions:
            return []
        self._session.add_all(suggestions)
        await self._session.flush()
        return suggestions

    async def update_status(
        self,
        suggestion: "Suggestion",
        decision: SuggestionDecision,
    ) -> None:
        """
        Атомарный UPDATE ... WHERE status = 'pending'.
        """
        from app.infrastructure.db.models.enums import SuggestionStatus
        stmt = (
            update(type(suggestion))
            .where(
                type(suggestion).id == suggestion.id,
                type(suggestion).status == SuggestionStatus.PENDING,
            )
            .values(
                status=_status_to_orm(decision.status),
                decided_by=decision.decided_by,
                decided_at=decision.decided_at,
            )
            .returning(type(suggestion).id)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        await self._session.flush()
        if updated is None:
            from app.domain.exceptions import SuggestionAlreadyDecidedError
            raise SuggestionAlreadyDecidedError(
                "Правка уже была принята или отклонена"
            )

    async def bulk_update_status(
        self,
        decisions: ReviewDecisions,
    ) -> int:
        """
        Один UPDATE ... WHERE id IN (...) AND status = 'pending'.
        """
        from app.infrastructure.db.models.enums import SuggestionStatus
        all_ids = [*decisions.accepted_ids, *decisions.rejected_ids]
        if not all_ids:
            return 0

        from app.infrastructure.db.models.suggestion import Suggestion as M
        stmt = (
            update(M)
            .where(
                M.analysis_job_id == decisions.analysis_job_id,
                M.status == SuggestionStatus.PENDING,
                M.id.in_(all_ids),
            )
            .values(
                status=case(
                    (M.id.in_(decisions.accepted_ids), _status_to_orm(SuggestionStatusVO.ACCEPTED)),
                    else_=_status_to_orm(SuggestionStatusVO.REJECTED),
                ),
                decided_by=decisions.decided_by,
                decided_at=decisions.decided_at,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount
