"""
Репозиторий проектов.

ИСПРАВЛЕНО: list_all/list_by_owner теперь принимают limit/offset (вместо возврата
всего результата целиком), добавлены count_all/count_by_owner для подсчёта общего числа.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.project import Project


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def create(self, project: Project) -> Project:
        self._session.add(project)
        await self._session.commit()
        await self._session.refresh(project)
        return project

    async def list_all(self, limit: int, offset: int) -> list[Project]:
        result = await self._session.execute(
            select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def count_all(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Project))
        return result.scalar_one()

    async def list_by_owner(self, owner_id: uuid.UUID, limit: int, offset: int) -> list[Project]:
        result = await self._session.execute(
            select(Project)
            .where(Project.owner_id == owner_id)
            .order_by(Project.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_by_owner(self, owner_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Project).where(Project.owner_id == owner_id)
        )
        return result.scalar_one()
