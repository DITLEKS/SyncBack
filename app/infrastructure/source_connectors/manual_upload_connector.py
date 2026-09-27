"""
ManualUploadConnector — скачивает файл из MinIO и парсит его в plain-text.

После P2: обрабатывает только SourceKind.FILE.
Текстовые заметки (бывший NOTE) теперь сохраняются в MinIO как .txt
и попадают сюда как обычные файлы с source_type=FILE.
"""
from app.domain.interfaces.source_connector import SourceKind, SourceMetadata, SourceRef
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry
from app.infrastructure.storage.minio_storage import MinioStorage


class ManualUploadConnector:
    def __init__(self, file_storage: MinioStorage, parser_registry: DocumentParserRegistry):
        self._storage = file_storage
        self._parsers = parser_registry

    def supports(self, source_type: SourceKind) -> bool:
        return source_type == SourceKind.FILE

    async def fetch(self, source: SourceRef) -> str:
        if source.type != SourceKind.FILE:
            raise ValueError(
                f"ManualUploadConnector поддерживает только FILE, получен: {source.type}"
            )
        if not source.storage_key:
            raise ValueError(f"storage_key отсутствует для источника {source.id}")
        raw_bytes = await self._storage.download(source.storage_key)
        parsed = self._parsers.parse_by_filename(source.storage_key, raw_bytes)
        return parsed.plain_text

    async def get_metadata(self, source: SourceRef) -> SourceMetadata:
        return SourceMetadata(
            name=source.name,
            type=source.type,
            uploaded_at=source.uploaded_at.isoformat(),
        )
