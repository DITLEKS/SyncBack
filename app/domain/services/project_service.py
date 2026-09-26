"""
Бизнес-логика проектов. Правило видимости (admin — всё, иначе только свои) продублировано
здесь для листинга и отдельно проверяется в api/deps.get_allowed_project для точечного доступа.

ДОБАВЛЕНО:
- update_project() — частичное обновление (переименование / изменение описания).
- delete_project() — каскадное удаление всех дочерних объектов и MinIO-файлов.

H2.2: сервис принимает ProjectPort вместо конкретного ProjectRepository.
Сonkrete SQLAlchemy-репозиторий по-прежнему передаётся из core/dependencies.py,
но тип в сигнатуре — порт домена.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.enums import UserRole
from app.domain.interfaces.file_storage import FileStorage
from app.domain.ports.project_port import ProjectPort

if TYPE_CHECKING:
    import uuid
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.user import User


class ProjectService:
    def __init__(self, project_repository: ProjectPort, file_storage: FileStorage):
        self._projects = project_repository
        self._storage = file_storage

    async def create_project(
        self, owner: "User", name: str, description: str | None = None
    ) -> "Project":
        from app.infrastructure.db.models.project import Project as ProjectModel
        project = ProjectModel(owner_id=owner.id, name=name, description=description)
        return await self._projects.create(project)

    async def list_projects_for_user(
        self, user: "User", limit: int, offset: int
    ) -> "tuple[list[Project], int]":
        if user.role == UserRole.ADMIN:
            items = await self._projects.list_all(limit=limit, offset=offset)
            total = await self._projects.count_all()
        else:
            items = await self._projects.list_by_owner(user.id, limit=limit, offset=offset)
            total = await self._projects.count_by_owner(user.id)
        return items, total

    async def update_project(
        self,
        project: "Project",
        name: str | None = None,
        description: str | None = None,
    ) -> "Project":
        """Частичное обновление. Передаём только те поля, которые пришли в запросе."""
        return await self._projects.update(project, name=name, description=description)

    async def delete_project(self, project: "Project") -> None:
        """
        Каскадное удаление:
        1. Загружаем все storage_key документов и источников проекта.
        2. Удаляем их из MinIO (ошибки — best-effort, логируем и продолжаем).
        3. Удаляем запись проекта — ON DELETE CASCADE в БД уберёт всё остальное.
        """
        storage_keys = await self._projects.collect_storage_keys(project.id)
        for key in storage_keys:
            try:
                await self._storage.delete(key)
            except Exception:  # noqa: BLE001
                # Best-effort: MinIO-объект мог быть уже удалён вручную.
                # Не прерываем удаление проекта из-за «битой» ссылки на файл.
                pass
        await self._projects.delete(project)
