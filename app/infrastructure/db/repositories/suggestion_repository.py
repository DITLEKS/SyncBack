"""SQLAlchemy-адаптер репозитория правок.

Реализует ISuggestionRepository. Содержит только инфраструктурный код:
  - нет логики commit/rollback (это зона IUnitOfWork)
  - статусы принимаются через SuggestionStatusVO (StrEnum) —
    значения совместимы с ORM-enum SuggestionStatus, поэтому явное преобразование не нужно.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import ISuggestionRepository
from app.domain.value_objects import ReviewDecisions, SuggestionDecision, SuggestionStatusVO
from app.infrastructure.db.models.enums import SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion


class SuggestionRepository(ISuggestionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_orm_status(vo: SuggestionStatusVO) -> SuggestionStatus:
        """VO -> ORM-enum. StrEnum-значения совпадают, но делаем явным для тайпчекера."""
        return SuggestionStatus(vo.value)

    # ------------------------------------------------------------------
    # ISuggestionRepository
    # ------------------------------------------------------------------

    async def bulk_create(self, suggestions: list[Suggestion]) -> list[Suggestion]:
        if not suggestions:
            return []
        self._session.add_all(suggestions)
        await self._session.flush()
        return suggestions

    async def get_by_id(self, suggestion_id: uuid.UUID) -> Suggestion | None:
        return await self._session.get(Suggestion, suggestion_id)

    async def list_by_analysis_job(
        self,
        analysis_job_id: uuid.UUID,
        pagination: object,  # PaginationParams — избегаем циклического импорта
    ) -> list[Suggestion]:
        from app.domain.value_objects import PaginationParams  # noqa: PLC0415
        p = pagination if isinstance(pagination, PaginationParams) else pagination
        result = await self._session.execute(
            select(Suggestion)
            .where(Suggestion.analysis_job_id == analysis_job_id)
            .order_by(Suggestion.created_at)
            .limit(p.limit)
            .offset(p.offset)
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
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Suggestion)
            .where(
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == self._to_orm_status(status),
            )
        )
        return result.scalar_one()

    async def list_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> list[Suggestion]:
        # Намеренно без LIMIT: этот метод является источником курсорной
        # итерации для экспорта и обязан возвращать все правки по заданному job.
        # Пагинация реализована на уровне
        # DocumentExportService._iter_accepted_changes_pages, а не здесь.
        result = await self._session.execute(
            select(Suggestion)
            .where(
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == self._to_orm_status(status),
            )
            .order_by(Suggestion.created_at)
        )
        return list(result.scalars().all())

    async def list_by_analysis_job_and_status_page(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
        limit: int,
        offset: int,
    ) -> list[Suggestion]:
        """Страничный вариант для курсорного обхода при экспорте.
        Используется исключительно из DocumentExportService._iter_accepted_changes_pages.
        """
        result = await self._session.execute(
            select(Suggestion)
            .where(
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == self._to_orm_status(status),
            )
            .order_by(Suggestion.created_at)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def list_ids_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(Suggestion.id).where(
                Suggestion.analysis_job_id == analysis_job_id,
                Suggestion.status == self._to_orm_status(status),
            )
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        suggestion: Suggestion,
        decision: SuggestionDecision,
    ) -> Suggestion | None:
        """CAS-UPDATE WHERE status = 'pending'. None — уже решена параллельным запросом."""
        stmt = (
            update(Suggestion)
            .where(
                Suggestion.id == suggestion.id,
                Suggestion.status == SuggestionStatus.PENDING,
            )
            .values(
                status=self._to_orm_status(decision.status),
                decided_by=decision.decided_by,
                decided_at=datetime.now(UTC),
            )
            .returning(Suggestion)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        await self._session.flush()
        if updated is None:
            return None
        await self._session.refresh(updated)
        return updated

    async def bulk_update_status(
        self,
        decisions: ReviewDecisions,
    ) -> list[Suggestion]:
        """Единый UPDATE для accepted + rejected через ReviewDecisions VO.
        Правки, решённые параллельно, автоматически пропускаются.
        """
        updated: list[Suggestion] = []

        for status_vo, ids in (
            (SuggestionStatusVO.ACCEPTED, decisions.accepted_ids),
            (SuggestionStatusVO.REJECTED, decisions.rejected_ids),
        ):
            if not ids:
                continue
            stmt = (
                update(Suggestion)
                .where(
                    Suggestion.id.in_(ids),
                    Suggestion.status == SuggestionStatus.PENDING,
                )
                .values(
                    status=self._to_orm_status(status_vo),
                    decided_by=decisions.decided_by,
                    decided_at=datetime.now(UTC),
                )
                .returning(Suggestion)
            )
            result = await self._session.execute(stmt)
            updated.extend(result.scalars().all())

        await self._session.flush()
        return updated
