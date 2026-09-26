"""Port (интерфейс) для persistence-операций над Project."""
from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from app.infrastructure.db.models.project import Project


@runtime_checkable
class ProjectPort(Protocol):
    """Все методы, которые используют доменные сервисы.

    Concrete-реализация — ProjectRepository в infrastructure/db/repositories.
    """

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None: ...

    async def create(self, project: Project) -> Project: ...

    async def list_all(self, limit: int, offset: int) -> list[Project]: ...

    async def count_all(self) -> int: ...

    async def list_by_owner(
        self, owner_id: uuid.UUID, limit: int, offset: int
    ) -> list[Project]: ...

    async def count_by_owner(self, owner_id: uuid.UUID) -> int: ...

    async def update(
        self,
        project: Project,
        name: str | None = None,
        description: str | None = None,
    ) -> Project: ...

    async def collect_storage_keys(self, project_id: uuid.UUID) -> list[str]: ...

    async def delete(self, project: Project) -> None: ...
