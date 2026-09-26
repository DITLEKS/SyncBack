"""
Бизнес-логика проектов.

Архитектурные правила:
  - Зависит только от IUnitOfWork (порт) и FileStorage (порт).
  - Нет импортов из app.infrastructure.* при выполнении.
  - Один uow.commit() на операцию.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import UserRoleVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.user import User


class ProjectService:
    def __init__(self, uow: IUnitOfWork, file_storage: FileStorage) -> None:
        self._uow = uow
        self._storage = file_storage

    async def create_project(
        self, owner: "User", name: str, description: str | None = None
    ) -> "Project":
        async with self._uow:
            project = await self._uow.projects.create(
                owner_id=owner.id, name=name, description=description
            )
            await self._uow.commit()
        return project

    async def list_projects_for_user(
        self, user: "User", limit: int, offset: int
    ) -> "tuple[list[Project], int]":
        async with self._uow:
            if user.role == UserRoleVO.ADMIN:
                items = await self._uow.projects.list_all(limit=limit, offset=offset)
                total = await self._uow.projects.count_all()
            else:
                items = await self._uow.projects.list_by_owner(
                    user.id, limit=limit, offset=offset
                )
                total = await self._uow.projects.count_by_owner(user.id)
        return items, total

    async def update_project(
        self,
        project: "Project",
        name: str | None = None,
        description: str | None = None,
    ) -> "Project":
        """Частичное обновление полей проекта."""
        async with self._uow:
            updated = await self._uow.projects.update(
                project, name=name, description=description
            )
            await self._uow.commit()
        return updated

    async def delete_project(self, project: "Project") -> None:
        """
        Каскадное удаление:
        1. Собираем storage_key всех файлов проекта.
        2. Удаляем их из MinIO (best-effort).
        3. Удаляем запись — ON DELETE CASCADE убирает дочерние строки.
        """
        async with self._uow:
            storage_keys = await self._uow.projects.collect_storage_keys(project.id)

        for key in storage_keys:
            try:
                await self._storage.delete(key)
            except Exception:  # noqa: BLE001
                # MinIO-объект мог быть уже удалён вручную — не прерываем удаление.
                pass

        async with self._uow:
            await self._uow.projects.delete(project)
            await self._uow.commit()
