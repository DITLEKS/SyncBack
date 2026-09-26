"""Репозиторий проектов."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exceptions import ProjectNotFoundError
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source import Source


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def get_for_user(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> Project:
        """Возвращает проект, если он принадлежит owner_id.

        Бросает ProjectNotFoundError если проект не найден.
        Бросает PermissionError если проект принадлежит другому пользователю.
        Роутер отвечает за маппинг этих исключений в HTTP 404/403.
        """
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} not found.")
        if project.owner_id != owner_id:
            raise PermissionError("Access denied: this project belongs to another user.")
        return project

    async def create(self, project: Project) -> Project:
        self._session.add(project)
        await self._session.flush()
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

    async def list_by_owner(
        self, owner_id: uuid.UUID, limit: int, offset: int
    ) -> list[Project]:
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
            select(func.count())
            .select_from(Project)
            .where(Project.owner_id == owner_id)
        )
        return result.scalar_one()

    async def update(
        self,
        project: Project,
        name: str | None = None,
        description: str | None = None,
    ) -> Project:
        """Обновляем только переданные (не-None) поля."""
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        await self._session.flush()
        await self._session.refresh(project)
        return project

    async def collect_storage_keys(self, project_id: uuid.UUID) -> list[str]:
        """Собирает MinIO-ключи документов и файл-источников проекта."""
        doc_keys_result = await self._session.execute(
            select(Document.storage_key).where(
                Document.project_id == project_id,
                Document.storage_key.isnot(None),
            )
        )
        source_keys_result = await self._session.execute(
            select(Source.storage_key).where(
                Source.project_id == project_id,
                Source.storage_key.isnot(None),
            )
        )
        keys: list[str] = []
        keys.extend(r[0] for r in doc_keys_result if r[0])
        keys.extend(r[0] for r in source_keys_result if r[0])
        return keys

    async def delete(self, project: Project) -> None:
        await self._session.delete(project)
        await self._session.flush()
