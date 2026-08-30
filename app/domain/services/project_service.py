"""
Бизнес-логика проектов. Правило видимости (admin — всё, иначе только свои) продублировано
здесь для листинга и отдельно проверяется в api/deps.get_allowed_project для точечного доступа.

ИСПРАВЛЕНО:
1. list_projects_for_user принимает limit/offset и возвращает (items, total).
2. create_project теперь принимает опциональный description.
"""

from app.infrastructure.db.models.enums import UserRole
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User
from app.infrastructure.db.repositories.project_repository import ProjectRepository


class ProjectService:
    def __init__(self, project_repository: ProjectRepository):
        self._projects = project_repository

    async def create_project(self, owner: User, name: str, description: str | None = None) -> Project:
        project = Project(owner_id=owner.id, name=name, description=description)
        return await self._projects.create(project)

    async def list_projects_for_user(self, user: User, limit: int, offset: int) -> tuple[list[Project], int]:
        if user.role == UserRole.ADMIN:
            items = await self._projects.list_all(limit=limit, offset=offset)
            total = await self._projects.count_all()
        else:
            items = await self._projects.list_by_owner(user.id, limit=limit, offset=offset)
            total = await self._projects.count_by_owner(user.id)
        return items, total
