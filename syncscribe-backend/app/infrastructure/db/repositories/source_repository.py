"""
Репозиторий источников.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.source import Source


class SourceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, source: Source) -> Source:
        self._session.add(source)
        await self._session.commit()
        await self._session.refresh(source)
        return source

    async def get_by_id(self, source_id: uuid.UUID) -> Source | None:
        return await self._session.get(Source, source_id)

    async def list_by_project(self, project_id: uuid.UUID) -> list[Source]:
        result = await self._session.execute(
            select(Source).where(Source.project_id == project_id).order_by(Source.uploaded_at.desc())
        )
        return list(result.scalars().all())

    async def get_many_by_ids(self, source_ids: list[uuid.UUID]) -> list[Source]:
        result = await self._session.execute(select(Source).where(Source.id.in_(source_ids)))
        return list(result.scalars().all())
