"""
Путь в репозитории: app/domain/interfaces/source_connector.py

ИСПРАВЛЕНО: раньше этот порт импортировал `SourceType` напрямую из
`app.infrastructure.db.models.enums`, из-за чего domain-слой формально зависел от
инфраструктуры (нарушение направления зависимостей hexagonal architecture). Теперь
порт определяет собственный SourceKind, не зависящий от SQLAlchemy/infrastructure.
Конвертация infrastructure.SourceType -> SourceKind теперь происходит на границе
(ManualUploadConnector, analysis_tasks.py), а не внутри domain-порта.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class SourceKind(str, Enum):
    FILE = "file"
    NOTE = "note"
    LINK = "link"


@dataclass
class SourceRef:
    id: uuid.UUID
    name: str
    type: SourceKind
    storage_key: str | None
    text_content: str | None
    url: str | None
    uploaded_at: datetime


@dataclass
class SourceMetadata:
    name: str
    type: SourceKind
    uploaded_at: str


class SourceConnector(Protocol):
    async def fetch(self, source: SourceRef) -> str: ...
    async def get_metadata(self, source: SourceRef) -> SourceMetadata: ...
    def supports(self, source_type: SourceKind) -> bool: ...
