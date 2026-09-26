"""Port (интерфейс) для persistence-операций над Document."""
from __future__ import annotations

import uuid
from typing import Literal, Protocol, runtime_checkable

from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus


@runtime_checkable
class DocumentPort(Protocol):
    """Все методы, которые используют доменные сервисы.

    Concrete-реализация — DocumentRepository в infrastructure/db/repositories.
    """

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None: ...

    async def list_by_project(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> list[Document]: ...

    async def count_by_project(self, project_id: uuid.UUID) -> int: ...

    async def create(self, document: Document) -> Document: ...

    async def update_status(
        self, document: Document, new_status: DocumentStatus
    ) -> Document: ...

    async def update_current_job(
        self, document: Document, job_id: uuid.UUID | None
    ) -> Document: ...

    async def delete(self, document: Document) -> None: ...

    async def list_all_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        status: DocumentStatus | None = None,
        search: str | None = None,
        sort_by: Literal["created_at", "updated_at", "title"] = "updated_at",
        sort_dir: Literal["asc", "desc"] = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]: ...

    async def increment_review_version(self, document: Document) -> Document: ...

    async def list_analyzable_for_project(
        self, project_id: uuid.UUID
    ) -> list[Document]: ...

    async def attach_sources(self, document: Document, sources: list) -> Document: ...
