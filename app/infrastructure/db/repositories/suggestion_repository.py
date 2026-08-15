"""Репозиторий правок."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
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
        result = await self._session.execute(select(Suggestion).where(Suggestion.analysis_job_id == analysis_job_id).order_by(Suggestion.created_at))
        return list(result.scalars().all())

    async def update_status(self, suggestion: Suggestion, status: SuggestionStatus, decided_by: uuid.UUID) -> Suggestion:
        suggestion.status = status
        suggestion.decided_by = decided_by
        suggestion.decided_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(suggestion)
        return suggestion

    async def bulk_update_status(self, suggestions: list[Suggestion], status: SuggestionStatus, decided_by: uuid.UUID) -> list[Suggestion]:
        if not suggestions:
            return []
        now = datetime.now(UTC)
        for suggestion in suggestions:
            suggestion.status = status
            suggestion.decided_by = decided_by
            suggestion.decided_at = now
        await self._session.commit()
        for suggestion in suggestions:
            await self._session.refresh(suggestion)
        return suggestions
