"""
Бизнес-логика источников истины: файл, текстовая заметка или ссылка.

ИСПРАВЛЕНО:
- list_sources принимает limit/offset и возвращает (items, total).
- P0-6: create_text_source / create_file_source принимают scope: SourceScope.
- P0-6: _assert_sources_mutable — гвард: запрещает изменение источников
  документа в статусах IN_PROGRESS и AWAITING_APPROVAL.
- A-1: конструктор принимает ISourceRepository вместо конкретного класса.
- Q-5: get_sources_for_project — один проход для found_ids и foreign-проверки.
"""

import uuid

from app.core.config import Settings, get_settings
from app.domain.exceptions import FileTooLargeError, SourceLockError, SourceNotFoundError
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.repository_interfaces import ISourceRepository
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus, SourceType
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.source_scope import SourceScope

_LOCKED_STATUSES = frozenset({
    DocumentStatus.IN_PROGRESS,
    DocumentStatus.AWAITING_APPROVAL,
})


class SourceService:
    def __init__(
        self,
        source_repository: ISourceRepository,
        file_storage: FileStorage,
        settings: Settings | None = None,
    ):
        self._sources = source_repository
        self._storage = file_storage
        self._settings = settings or get_settings()

    @staticmethod
    def _assert_sources_mutable(document: Document) -> None:
        """P0-6: Выбросить SourceLockError, если источники менять нельзя."""
        if document.status in _LOCKED_STATUSES:
            raise SourceLockError(
                f"Нельзя изменить источники документа в статусе '{document.status.value}'. "
                "Дождитесь завершения анализа или переведите документ обратно в черновик."
            )

    async def create_text_source(
        self,
        project: Project,
        name: str,
        source_type: SourceType,
        text_content: str | None,
        url: str | None,
        scope: SourceScope = SourceScope.PROJECT,
    ) -> Source:
        source = Source(
            project_id=project.id,
            name=name,
            type=source_type,
            text_content=text_content,
            url=url,
            scope=scope,
        )
        return await self._sources.create(source)

    async def create_file_source(
        self,
        project: Project,
        name: str,
        filename: str,
        content: bytes,
        content_type: str,
        scope: SourceScope = SourceScope.PROJECT,
    ) -> Source:
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ")

        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/{filename}"
        await self._storage.upload(storage_key, content, content_type)

        source = Source(
            id=source_id,
            project_id=project.id,
            name=name,
            type=SourceType.FILE,
            storage_key=storage_key,
            scope=scope,
        )
        try:
            return await self._sources.create(source)
        except Exception:
            await self._storage.delete(storage_key)
            raise

    async def list_sources(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[Source], int]:
        items = await self._sources.list_by_project(project_id, limit=limit, offset=offset)
        total = await self._sources.count_by_project(project_id)
        return items, total

    async def get_sources_for_project(
        self, project_id: uuid.UUID, source_ids: list[uuid.UUID]
    ) -> list[Source]:
        """Q-5: один проход по sources — вместо двух set-операций."""
        sources = await self._sources.get_many_by_ids(source_ids)

        source_ids_set = set(source_ids)
        found_ids: set[uuid.UUID] = set()
        foreign: list[uuid.UUID] = []

        for s in sources:
            found_ids.add(s.id)
            if s.project_id != project_id:
                foreign.append(s.id)

        missing = source_ids_set - found_ids
        if missing:
            raise SourceNotFoundError(f"Источники не найдены: {missing}")
        if foreign:
            raise SourceNotFoundError(f"Источники не принадлежат проекту {project_id}: {foreign}")

        return sources

    async def replace_document_sources(
        self,
        document: Document,
        source_ids: list[uuid.UUID],
    ) -> list[Source]:
        """Атомарная замена набора источников документа (P0-6)."""
        self._assert_sources_mutable(document)
        sources = await self.get_sources_for_project(document.project_id, source_ids)
        return await self._sources.replace_document_sources(document.id, sources)
