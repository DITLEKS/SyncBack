"""
Бизнес-логика источников истины.

MYPY-FIX: все методы получили явные аннотации возврата.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Callable, Awaitable

from app.core.config import Settings, get_settings
from app.domain.exceptions import FileTooLargeError, SourceLockError, SourceNotFoundError
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import DocumentStatusVO, SourceScopeVO, SourceTypeVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.source import Source

_LOCKED_STATUSES: frozenset[DocumentStatusVO] = frozenset({
    DocumentStatusVO.IN_PROGRESS,
    DocumentStatusVO.AWAITING_APPROVAL,
})

_ActiveJobChecker = Callable[[uuid.UUID], Awaitable[None]]


class SourceService:
    def __init__(
        self,
        uow: IUnitOfWork,
        file_storage: FileStorage,
        settings: Settings | None = None,
    ) -> None:
        self._uow = uow
        self._storage = file_storage
        self._settings = settings or get_settings()

    @staticmethod
    def _assert_sources_mutable(document: "Document") -> None:
        if document.status in _LOCKED_STATUSES:
            raise SourceLockError(
                f"Нельзя изменить источники документа в статусе '{document.status.value}'."
            )

    async def _attach_if_document_scope(
        self,
        source_id: uuid.UUID,
        scope: SourceScopeVO,
        document_id: uuid.UUID | None,
    ) -> None:
        if scope is SourceScopeVO.DOCUMENT and document_id is not None:
            await self._uow.sources.attach_to_document(source_id, document_id)

    async def create_note_source(
        self,
        project: "Project",
        name: str,
        text_content: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> "Source":
        if len(text_content.encode()) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(
                f"Текст превышает лимит {self._settings.max_upload_size_mb} МБ"
            )
        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/note.txt"
        try:
            async with self._uow:
                await self._storage.upload(
                    storage_key,
                    text_content.encode("utf-8"),
                    "text/plain; charset=utf-8",
                )
                source = await self._uow.sources.create_with_id(
                    source_id=source_id,
                    project_id=project.id,
                    name=name,
                    source_type=SourceTypeVO.FILE,
                    storage_key=storage_key,
                    scope=scope,
                )
                await self._attach_if_document_scope(source.id, scope, document_id)
                await self._uow.commit()
        except Exception:
            await self._storage.delete(storage_key)
            raise
        return source

    async def create_url_source(
        self,
        project: "Project",
        name: str,
        url: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> "Source":
        async with self._uow:
            source = await self._uow.sources.create_url(
                project_id=project.id,
                name=name,
                url=url,
                scope=scope,
            )
            await self._attach_if_document_scope(source.id, scope, document_id)
            await self._uow.commit()
        return source

    async def create_file_source(
        self,
        project: "Project",
        name: str,
        content: bytes,
        content_type: str,
        original_filename: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> "Source":
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(
                f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ"
            )
        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/{original_filename}"
        try:
            async with self._uow:
                await self._storage.upload(storage_key, content, content_type)
                source = await self._uow.sources.create_with_id(
                    source_id=source_id,
                    project_id=project.id,
                    name=name,
                    source_type=SourceTypeVO.FILE,
                    storage_key=storage_key,
                    scope=scope,
                )
                await self._attach_if_document_scope(source.id, scope, document_id)
                await self._uow.commit()
        except Exception:
            await self._storage.delete(storage_key)
            raise
        return source

    async def list_sources(
        self,
        project_id: uuid.UUID,
        limit: int,
        offset: int,
        scope: SourceScopeVO | None = None,
    ) -> tuple[list["Source"], int]:
        async with self._uow:
            items = await self._uow.sources.list_by_project(
                project_id, limit=limit, offset=offset, scope=scope
            )
            total = await self._uow.sources.count_by_project(project_id)
        return items, total

    async def get_source(
        self, project_id: uuid.UUID, source_id: uuid.UUID
    ) -> "Source":
        async with self._uow:
            source = await self._uow.sources.get_by_id(source_id)
        if source is None or source.project_id != project_id:
            raise SourceNotFoundError(
                f"Источник {source_id} не найден в проекте {project_id}"
            )
        return source

    async def get_sources_for_project(
        self, project_id: uuid.UUID, source_ids: list[uuid.UUID]
    ) -> list["Source"]:
        async with self._uow:
            sources = await self._uow.sources.get_many_by_ids(source_ids)
        found_ids = {s.id for s in sources}
        missing = set(source_ids) - found_ids
        if missing:
            raise SourceNotFoundError(f"Источники не найдены: {missing}")
        foreign = [s.id for s in sources if s.project_id != project_id]
        if foreign:
            raise SourceNotFoundError(
                f"Источники не принадлежат проекту {project_id}: {foreign}"
            )
        return sources

    async def list_sources_for_documents(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list["Source"]]:
        if not document_ids:
            return {}
        async with self._uow:
            pairs = await self._uow.sources.list_by_document_ids(
                project_id, document_ids
            )
        result: dict[uuid.UUID, list["Source"]] = defaultdict(list)
        for source, doc_id in pairs:
            result[doc_id].append(source)
        return dict(result)

    async def delete_source_with_guard(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        active_job_checker: "_ActiveJobChecker | None" = None,
    ) -> None:
        async with self._uow:
            source = await self._uow.sources.get_by_id(source_id)
            if source is None or source.project_id != project_id:
                raise SourceNotFoundError(
                    f"Источник {source_id} не найден в проекте {project_id}"
                )
            document_id = await self._uow.sources.get_primary_document_id_for_source(
                source_id
            )
            if document_id is not None and active_job_checker is not None:
                await active_job_checker(document_id)
            if source.storage_key:
                await self._storage.delete(source.storage_key)
            await self._uow.sources.delete(source_id)
            await self._uow.commit()
