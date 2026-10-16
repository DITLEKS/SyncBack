"""Suggestion repository implementation."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.interfaces.repositories import ISuggestionRepository
from app.infrastructure.db.models.enums import SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.models.suggestion_source import suggestion_sources


class SuggestionRepository(ISuggestionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _query(self):
        return select(Suggestion).options(selectinload(Suggestion.sources))

    async def get_by_id(self, suggestion_id: uuid.UUID) -> Suggestion | None:
        result = await self._session.execute(self._query().where(Suggestion.id == suggestion_id))
        return result.scalar_one_or_none()

    async def list_by_document(self, document_id: uuid.UUID) -> list[Suggestion]:
        result = await self._session.execute(self._query().where(Suggestion.document_id == document_id).order_by(Suggestion.seq))
        return list(result.scalars().all())

    async def list_by_analysis_job(self, analysis_job_id: uuid.UUID) -> list[Suggestion]:
        result = await self._session.execute(self._query().where(Suggestion.analysis_job_id == analysis_job_id).order_by(Suggestion.seq))
        return list(result.scalars().unique().all())

    async def bulk_create(self, suggestions: list[Suggestion], *, source_ids: Sequence[uuid.UUID] = ()) -> list[Suggestion]:
        if not suggestions:
            return []
        self._session.add_all(suggestions)
        await self._session.flush()
        links = [
            {"suggestion_id": suggestion.id, "source_id": source_id}
            for suggestion in suggestions
            for source_id in dict.fromkeys(source_ids)
        ]
        if links:
            await self._session.execute(suggestion_sources.insert(), links)
            await self._session.flush()
        return suggestions

    async def merge_duplicates_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int:
        suggestions = await self.list_by_analysis_job(analysis_job_id)
        by_key: dict[tuple[object, ...], Suggestion] = {}
        merged = 0
        for suggestion in suggestions:
            key = self._identity_key(suggestion)
            target = by_key.get(key)
            if target is None:
                by_key[key] = suggestion
                continue
            known_source_ids = {source.id for source in target.sources}
            target.sources.extend(source for source in suggestion.sources if source.id not in known_source_ids)
            await self._session.delete(suggestion)
            merged += 1
        if merged:
            await self._session.flush()
        return merged

    @staticmethod
    def _identity_key(suggestion: Suggestion) -> tuple[object, ...]:
        return (
            suggestion.section_ref,
            suggestion.change_type,
            suggestion.original_text,
            suggestion.suggested_text,
            suggestion.block_id,
            suggestion.start_offset,
            suggestion.end_offset,
        )

    async def update_status(self, suggestion_id: uuid.UUID, status: SuggestionStatus, *, edited_text: str | None = None) -> Suggestion | None:
        suggestion = await self.get_by_id(suggestion_id)
        if suggestion is None:
            return None
        suggestion.status = status
        suggestion.edited_text = edited_text
        await self._session.flush()
        return suggestion

    async def count_by_analysis_job_and_status(self, analysis_job_id: uuid.UUID, status: SuggestionStatus) -> int:
        result = await self._session.execute(select(func.count()).select_from(Suggestion).where(Suggestion.analysis_job_id == analysis_job_id, Suggestion.status == status))
        return int(result.scalar_one())
