"""
Репозиторий проектов.

ДОБАВЛЕНО:
- update() — изменяет name/description (partial update, None-поля игнорируются).
- collect_storage_keys() — собирает MinIO-ключи всех документов и файл-источников проекта.
- delete() — удаляет запись проекта; каскад в БД удаляет дочерние таблицы.
- get_for_user() — возвращает проект или выбрасывает HTTP 403/404 (ownership guard).
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source import Source


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def get_for_user(
        self, project_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Project:
        """
        Возвращает проект, если он принадлежит owner_id.
        - 404 если проекта нет
        - 403 если проект существует, но принадлежит другому пользователю

        Используется везде, где нужна проверка ownership перед мутацией.
        """
        project = await self._session.get(Project, project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_id} not found.",
            )
        if project.owner_id != owner_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: this project belongs to another user.",
            )
        return project

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
        await self._session.commit()
        await self._session.refresh(project)
        return project

    async def collect_storage_keys(self, project_id: uuid.UUID) -> list[str]:
        """
        Собирает MinIO-ключи для:
        - всех документов проекта (Document.storage_key)
        - всех файл-источников проекта (Source.storage_key, только file-тип)
        """
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
        await self._session.commit()
