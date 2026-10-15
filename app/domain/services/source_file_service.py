"""Чтение исходного файла источника для скачивания через авторизованный API."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from app.domain.exceptions import UnsupportedFormatError
from app.domain.interfaces.file_storage import FileStorage
from app.domain.services.source_service import SourceService


@dataclass(frozen=True, slots=True)
class SourceFile:
    content: bytes
    filename: str


class SourceFileService:
    def __init__(self, sources: SourceService, storage: FileStorage) -> None:
        self._sources = sources
        self._storage = storage

    async def get_file(self, project_id: uuid.UUID, source_id: uuid.UUID) -> SourceFile:
        # Проверка принадлежности проекту выполняется до обращения к хранилищу.
        source = await self._sources.get_source(project_id, source_id)
        if not source.storage_key:
            raise UnsupportedFormatError(
                "Источник-ссылка не содержит файла. Откройте его по адресу."
            )
        suffix = Path(source.storage_key).suffix
        filename = source.name.strip() or "source"
        if suffix and not filename.lower().endswith(suffix.lower()):
            filename += suffix
        content = await self._storage.download(source.storage_key)
        return SourceFile(content=content, filename=filename)
