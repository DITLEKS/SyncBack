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
- OPT-1: в list_with_total удалён мёртвый код (count_q / items_q строились, но
  не исполнялись при непустом результате). window-function func.count().over()
  корректно возвращает 0 на пустой выборке, поэтому отдельный count_q для
  пустого случая тоже лишний. Итого: один SELECT вместо двух.
- RESET: reset_status() — UPDATE WHERE status != PENDING, обнуляет
  decided_by/decided_at, возвращает обновлённый объект или None если правка
  уже PENDING.
- C-3 (issue #37): bulk_reject_all — зеркало bulk_accept_all.
- M-1 (issue #37): bulk_update_status scope-фильтр исправлен:
  decisions.document_id сравнивается с M.document_id (денормализованная
  колонка), а не с M.analysis_job_id.
- NEW-1: delete_by_analysis_job — bulk DELETE всех правок job одним запросом;
  вызывается в create_job при повторном анализе (ERROR/CANCELLED → DRAFT).
- NEW-2: reset_to_pending_by_job — bulk UPDATE всех правок job → PENDING;
  вызывался в reset_analysis() но отсутствовал в реализации.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import case, delete, func, select, update
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

    async def get_by_id(self, suggestion_id: uuid.UUID) -> Suggestion | None:
        from app.infrastructure.db.models.suggestion import Suggestion as M

        return await self._session.get(M, suggestion_id)

    async def list_by_analysis_job(
        self,
        analysis_job_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
        status: SuggestionStatusVO | None = None,
    ) -> list[Suggestion]:
        from app.infrastructure.db.models.suggestion import Suggestion as M

        q = select(M).where(M.analysis_job_id == analysis_job_id)
        if status is not None:
            q = q.where(M.status == _status_to_orm(status))
        q = q.order_by(M.created_at.asc(), M.id.asc()).offset(offset)
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
    ) -> tuple[list[Suggestion], int]:
        """OPT-1: один SELECT с window-функцией вместо двух запросов.

        func.count().over() вычисляется ДО применения LIMIT/OFFSET в PostgreSQL,
        поэтому возвращает полный COUNT строк фильтрованной выборки.
        Поведение покрыто тестом test_list_with_total_window_count.
        """
        from app.infrastructure.db.models.suggestion import Suggestion as M

        where_clauses = [M.analysis_job_id == analysis_job_id]
        if status is not None:
            where_clauses.append(M.status == _status_to_orm(status))

        rows = (
            await self._session.execute(
                select(M, func.count().over().label("total"))
                .where(*where_clauses)
                .order_by(M.created_at.asc(), M.id.asc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

        if not rows:
            return [], 0
        items = [r[0] for r in rows]
        total = rows[0][1]
        return items, total

    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int:
        from app.infrastructure.db.models.suggestion import Suggestion as M

        result = await self._session.execute(
            select(func.count()).select_from(M).where(M.analysis_job_id == analysis_job_id)
        )
        return result.scalar_one()

    async def count_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> int:
        from app.infrastructure.db.models.suggestion import Suggestion as M

        result = await self._session.execute(
            select(func.count())
            .select_from(M)
            .where(
                M.analysis_job_id == analysis_job_id,
                M.status == _status_to_orm(status),
            )
        )
        return result.scalar_one()

    async def list_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> list[Suggestion]:
        from app.infrastructure.db.models.suggestion import Suggestion as M

        result = await self._session.execute(
            select(M)
            .where(
                M.analysis_job_id == analysis_job_id,
                M.status == _status_to_orm(status),
            )
            .order_by(M.created_at.asc(), M.id.asc())
        )
        return list(result.scalars().all())

    async def list_by_analysis_job_and_status_page(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
        limit: int,
        offset: int = 0,
    ) -> list[Suggestion]:
        """
        Постраничный вариант list_by_analysis_job_and_status для стримингового
        экспорта. Покрывается индексом ix_suggestions_job_status (миграция 0015).
        """
        from app.infrastructure.db.models.suggestion import Suggestion as M

        result = await self._session.execute(
            select(M)
            .where(
                M.analysis_job_id == analysis_job_id,
                M.status == _status_to_orm(status),
            )
            .order_by(M.created_at.asc(), M.id.asc())
            .limit(limit)
            .offset(offset)
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

    async def bulk_create(self, suggestions: list[Suggestion]) -> list[Suggestion]:
        if not suggestions:
            return []
        self._session.add_all(suggestions)
        await self._session.flush()
        return suggestions

    async def update_status(
        self,
        suggestion: Suggestion,
        decision: SuggestionDecision,
    ) -> None:
        """
        Атомарный UPDATE ... WHERE status = 'pending'.
        Raises SuggestionAlreadyDecidedError если строка не затронута.
        """
        from app.infrastructure.db.models.enums import SuggestionStatus

        now = datetime.now(UTC)
        stmt = (
            update(type(suggestion))
            .where(
                type(suggestion).id == suggestion.id,
                type(suggestion).status == SuggestionStatus.PENDING,
            )
            .values(
                status=_status_to_orm(decision.status),
                decided_by=decision.user_id,
                decided_at=now,
            )
            .returning(type(suggestion).id)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        await self._session.flush()
        if updated is None:
            from app.domain.exceptions import SuggestionAlreadyDecidedError

            raise SuggestionAlreadyDecidedError("Правка уже была принята или отклонена")

    async def bulk_update_status(
        self,
        decisions: ReviewDecisions,
    ) -> int:
        """
        Один UPDATE ... WHERE id IN (...) AND status = 'pending'.

        M-1 (issue #37): scope-фильтр исправлен — decisions.document_id
        сравнивается с денормализованной колонкой M.document_id, а не
        с M.analysis_job_id (что было семантически неверно).
        """
        from app.infrastructure.db.models.enums import SuggestionStatus

        all_decisions = decisions.decisions
        if not all_decisions:
            return 0

        accepted_ids = [
            d.suggestion_id for d in all_decisions if d.status == SuggestionStatusVO.ACCEPTED
        ]
        all_ids = [d.suggestion_id for d in all_decisions]

        from app.infrastructure.db.models.suggestion import Suggestion as M

        where_clauses = [
            M.document_id == decisions.document_id,
            M.status == SuggestionStatus.PENDING,
            M.id.in_(all_ids),
        ]
        if decisions.analysis_job_id is not None:
            where_clauses.append(M.analysis_job_id == decisions.analysis_job_id)
        stmt = (
            update(M)
            .where(*where_clauses)
            .values(
                status=case(
                    (M.id.in_(accepted_ids), _status_to_orm(SuggestionStatusVO.ACCEPTED)),
                    else_=_status_to_orm(SuggestionStatusVO.REJECTED),
                ),
                decided_by=decisions.user_id,
                decided_at=func.now(),
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def bulk_accept_all(
        self,
        analysis_job_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[Suggestion]:
        """Принять все PENDING-правки одним UPDATE, вернуть обновлённые объекты."""
        from app.infrastructure.db.models.enums import SuggestionStatus
        from app.infrastructure.db.models.suggestion import Suggestion as M

        now = datetime.now(UTC)
        stmt = (
            update(M)
            .where(
                M.analysis_job_id == analysis_job_id,
                M.status == SuggestionStatus.PENDING,
            )
            .values(
                status=SuggestionStatus.ACCEPTED,
                decided_by=user_id,
                decided_at=now,
            )
            .returning(M.id)
        )
        result = await self._session.execute(stmt)
        updated_ids = list(result.scalars().all())
        await self._session.flush()
        if not updated_ids:
            return []
        rows = await self._session.execute(select(M).where(M.id.in_(updated_ids)))
        return list(rows.scalars().all())

    async def bulk_reject_all(
        self,
        analysis_job_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[Suggestion]:
        """C-3 (issue #37): отклонить все PENDING-правки одним UPDATE.

        Зеркало bulk_accept_all — один UPDATE WHERE status=PENDING,
        затем SELECT обновлённых объектов из identity map / БД.
        """
        from app.infrastructure.db.models.enums import SuggestionStatus
        from app.infrastructure.db.models.suggestion import Suggestion as M

        now = datetime.now(UTC)
        stmt = (
            update(M)
            .where(
                M.analysis_job_id == analysis_job_id,
                M.status == SuggestionStatus.PENDING,
            )
            .values(
                status=SuggestionStatus.REJECTED,
                decided_by=user_id,
                decided_at=now,
            )
            .returning(M.id)
        )
        result = await self._session.execute(stmt)
        updated_ids = list(result.scalars().all())
        await self._session.flush()
        if not updated_ids:
            return []
        rows = await self._session.execute(select(M).where(M.id.in_(updated_ids)))
        return list(rows.scalars().all())

    async def reset_status(
        self,
        suggestion: Suggestion,
    ) -> Suggestion | None:
        """Сбросить решение правки обратно в PENDING.

        Атомарный UPDATE ... WHERE status != 'pending' AND id = ?.
        Возвращает обновлённый объект или None если правка уже PENDING
        (сбрасывать нечего — идемпотентно с точки зрения репозитория,
        но сервис вернёт SuggestionResetNotAllowedError).
        """
        from app.infrastructure.db.models.enums import SuggestionStatus
        from app.infrastructure.db.models.suggestion import Suggestion as M

        stmt = (
            update(M)
            .where(
                M.id == suggestion.id,
                M.status != SuggestionStatus.PENDING,
            )
            .values(
                status=SuggestionStatus.PENDING,
                decided_by=None,
                decided_at=None,
            )
            .returning(M.id)
        )
        result = await self._session.execute(stmt)
        updated_id = result.scalar_one_or_none()
        await self._session.flush()
        if updated_id is None:
            return None
        await self._session.refresh(suggestion)
        return suggestion

    async def delete_by_analysis_job(
        self,
        analysis_job_id: uuid.UUID,
    ) -> int:
        """NEW-1: bulk DELETE всех правок job одним запросом.

        DELETE FROM suggestions WHERE analysis_job_id = ?.
        Возвращает rowcount — количество удалённых строк.

        Идемпотентен: если правок нет — возвращает 0, не бросает исключений.
        Вызывается в AnalysisJobService.create_job() при повторном запуске
        анализа (статус документа ERROR или CANCELLED), до создания новой job.
        """
        from app.infrastructure.db.models.suggestion import Suggestion as M

        stmt = delete(M).where(M.analysis_job_id == analysis_job_id)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def reset_to_pending(
        self,
        analysis_job_id: uuid.UUID,
        ids: Sequence[uuid.UUID] | None = None,
    ) -> list[uuid.UUID]:
        from app.infrastructure.db.models.enums import SuggestionStatus
        from app.infrastructure.db.models.suggestion import Suggestion as M

        where_clauses = [
            M.analysis_job_id == analysis_job_id,
            M.status != SuggestionStatus.PENDING,
        ]
        if ids is not None:
            if not ids:
                return []
            where_clauses.append(M.id.in_(list(ids)))
        stmt = (
            update(M)
            .where(*where_clauses)
            .values(status=SuggestionStatus.PENDING, decided_by=None, decided_at=None)
            .returning(M.id)
        )
        result = await self._session.execute(stmt)
        reset_ids = list(result.scalars().all())
        await self._session.flush()
        return reset_ids

    async def reset_to_pending_by_job(
        self,
        analysis_job_id: uuid.UUID,
    ) -> int:
        """NEW-2: bulk UPDATE всех правок job обратно в PENDING.

        UPDATE suggestions
           SET status = 'pending', decided_by = NULL, decided_at = NULL
         WHERE analysis_job_id = ? AND status != 'pending'.
        Возвращает количество затронутых строк.

        Идемпотентен: если все правки уже PENDING — возвращает 0.
        Вызывается в AnalysisJobService.reset_analysis().
        """
        from app.infrastructure.db.models.enums import SuggestionStatus
        from app.infrastructure.db.models.suggestion import Suggestion as M

        stmt = (
            update(M)
            .where(
                M.analysis_job_id == analysis_job_id,
                M.status != SuggestionStatus.PENDING,
            )
            .values(
                status=SuggestionStatus.PENDING,
                decided_by=None,
                decided_at=None,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount
