"""
Бизнес-логика источников истины: файл, текстовая заметка или ссылка.

ИСПРАВЛЕНО: list_sources теперь принимает limit/offset и возвращает
(items, total) вместо всего списка целиком.
"""

import uuid

from app.core.config import Settings, get_settings
from app.domain.exceptions import FileTooLargeError, SourceNotFoundError
from app.domain.interfaces.file_storage import FileStorage
from app.infrastructure.db.models.enums import SourceType
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.repositories.source_repository import SourceRepository


class SourceService:
    def __init__(
        self,
        source_repository: SourceRepository,
        file_storage: FileStorage,
        settings: Settings | None = None,
    ):
        self._sources = source_repository
        self._storage = file_storage
        self._settings = settings or get_settings()

    async def create_text_source(
        self, project: Project, name: str, source_type: SourceType, text_content: str | None, url: str | None
    ) -> Source:
        source = Source(project_id=project.id, name=name, type=source_type, text_content=text_content, url=url)
        return await self._sources.create(source)

    async def create_file_source(
        self, project: Project, name: str, filename: str, content: bytes, content_type: str
    ) -> Source:
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ")

        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/{filename}"
        await self._storage.upload(storage_key, content, content_type)

        source = Source(id=source_id, project_id=project.id, name=name, type=SourceType.FILE, storage_key=storage_key)
        try:
            return await self._sources.create(source)
        except Exception:
            await self._storage.delete(storage_key)
            raise

    async def list_sources(self, project_id: uuid.UUID, limit: int, offset: int) -> tuple[list[Source], int]:
        items = await self._sources.list_by_project(project_id, limit=limit, offset=offset)
        total = await self._sources.count_by_project(project_id)
        return items, total

    async def get_sources_for_project(self, project_id: uuid.UUID, source_ids: list[uuid.UUID]) -> list[Source]:
        sources = await self._sources.get_many_by_ids(source_ids)
        found_ids = {s.id for s in sources}
        missing = set(source_ids) - found_ids
        if missing:
            raise SourceNotFoundError(f"Источники не найдены: {missing}")

        foreign = [s.id for s in sources if s.project_id != project_id]
        if foreign:
            raise SourceNotFoundError(f"Источники не принадлежат проекту {project_id}: {foreign}")

        return sources
