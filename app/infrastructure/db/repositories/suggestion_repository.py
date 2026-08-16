"""Репозиторий правок."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
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

    async def list_by_analysis_job(self, analysis_job_id: uuid.UUID) -> list[Suggestion]:
        result = await self._session.execute(
            select(Suggestion).where(Suggestion.analysis_job_id == analysis_job_id).order_by(Suggestion.created_at)
        )
        return list(result.scalars().all())

    async def update_status(self, suggestion: Suggestion, status: SuggestionStatus, decided_by: uuid.UUID) -> Suggestion | None:
        """
        ИСПРАВЛЕНО: раньше это был обычный ORM update без проверки текущего статуса на
        уровне БД (загрузили объект → изменили атрибуты → закоммитили). Между
        чтением и записью два параллельных запроса могли оба посчитать suggestion ещё
        не решённым и оба применить решение — в БД тихо сохранился бы результат
        последнего commit'а без какой-либо ошибки конфликта. Теперь UPDATE условный
        (WHERE status = 'pending') и атомарный на уровне БД: если suggestion уже был решён
        другим запросом, обновление не применится, и вызывающий код узнаёт об этом по
        возвращаемому None.
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
        ИСПРАВЛЕНО: раньше принимала уже загруженные ORM-объекты и обновляла их
        атрибуты по одному в Python-цикле — между выборкой "ещё не решённых" правок и
        коммитом параллельный запрос мог успеть принять/отклонить часть из них, и
        bulk-accept тихо перезаписал бы это решение. Теперь принимает только id и делает
        один атомарный UPDATE ... WHERE status = 'pending' — правки, решённые параллельно
        между выборкой id и этим запросом, автоматически не попадут в обновление.
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
