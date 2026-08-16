"""
Путь в репозитории: app/infrastructure/source_connectors/manual_upload_connector.py

ИСПРАВЛЕНО: типы domain-порта теперь свои собственные (SourceKind), а не
импортируются из infrastructure напрямую в domain. Эта реализация работает
только с domain-типами; конвертация из infrastructure.SourceType в SourceKind вынесена в
точку вызова (app/workers/tasks/analysis_tasks.py), где собирается SourceRef из ORM-модели.
"""
from app.domain.interfaces.source_connector import SourceKind, SourceMetadata, SourceRef
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry
from app.infrastructure.storage.minio_storage import MinioStorage


class ManualUploadConnector:
    def __init__(self, file_storage: MinioStorage, parser_registry: DocumentParserRegistry):
        self._storage = file_storage
        self._parsers = parser_registry

    def supports(self, source_type: SourceKind) -> bool:
        return source_type in (SourceKind.FILE, SourceKind.NOTE, SourceKind.LINK)

    async def fetch(self, source: SourceRef) -> str:
        if source.type == SourceKind.NOTE:
            return source.text_content or ""
        if source.type == SourceKind.LINK:
            return f"Ссылка на источник (контент по URL не загружается автоматически в MVP): {source.url}"
        if source.type == SourceKind.FILE:
            raw_bytes = await self._storage.download(source.storage_key)
            parsed = self._parsers.parse_by_filename(source.storage_key, raw_bytes)
            return parsed.plain_text
        raise ValueError(f"Неизвестный тип источника: {source.type}")

    async def get_metadata(self, source: SourceRef) -> SourceMetadata:
        return SourceMetadata(name=source.name, type=source.type, uploaded_at=source.uploaded_at.isoformat())
