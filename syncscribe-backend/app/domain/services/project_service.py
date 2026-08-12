"""
Бизнес-логика проектов. Правило видимости (admin — всё, иначе только свои) продублировано
здесь для листинга и отдельно проверяется в api/deps.get_allowed_project для точечного доступа —
это два разных сценария (список "что я вижу" против "имею ли доступ к конкретному id"),
поэтому дублирования зависимостей друг от друга здесь нет.
"""

from app.infrastructure.db.models.enums import UserRole
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User
from app.infrastructure.db.repositories.project_repository import ProjectRepository


class ProjectService:
    def __init__(self, project_repository: ProjectRepository):
        self._projects = project_repository

    async def create_project(self, owner: User, name: str) -> Project:
        project = Project(owner_id=owner.id, name=name)
        return await self._projects.create(project)

    async def list_projects_for_user(self, user: User) -> list[Project]:
        if user.role == UserRole.ADMIN:
            return await self._projects.list_all()
        return await self._projects.list_by_owner(user.id)
