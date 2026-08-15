"""
Репозиторий проектов.
"""

import uuid

from sqlalchemy import select
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

    async def list_all(self) -> list[Project]:
        result = await self._session.execute(select(Project).order_by(Project.created_at.desc()))
        return list(result.scalars().all())

    async def list_by_owner(self, owner_id: uuid.UUID) -> list[Project]:
        result = await self._session.execute(
            select(Project).where(Project.owner_id == owner_id).order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())
