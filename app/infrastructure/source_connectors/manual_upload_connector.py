from app.domain.interfaces.source_connector import SourceMetadata, SourceRef
from app.infrastructure.db.models.enums import SourceType
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry
from app.infrastructure.storage.minio_storage import MinioStorage


class ManualUploadConnector:
    def __init__(self, file_storage: MinioStorage, parser_registry: DocumentParserRegistry):
        self._storage = file_storage
        self._parsers = parser_registry

    def supports(self, source_type: SourceType) -> bool:
        return source_type in (SourceType.FILE, SourceType.NOTE, SourceType.LINK)

    async def fetch(self, source: SourceRef) -> str:
        if source.type == SourceType.NOTE:
            return source.text_content or ""
        if source.type == SourceType.LINK:
            return f"Ссылка на источник (контент по URL не загружается автоматически в MVP): {source.url}"
        if source.type == SourceType.FILE:
            raw_bytes = await self._storage.download(source.storage_key)
            parsed = self._parsers.parse_by_filename(source.storage_key, raw_bytes)
            return parsed.plain_text
        raise ValueError(f"Неизвестный тип источника: {source.type}")

    async def get_metadata(self, source: SourceRef) -> SourceMetadata:
        return SourceMetadata(name=source.name, type=source.type, uploaded_at=source.uploaded_at.isoformat())
