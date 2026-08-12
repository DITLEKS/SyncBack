import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from app.infrastructure.db.models.enums import SourceType


@dataclass
class SourceRef:
    id: uuid.UUID
    name: str
    type: SourceType
    storage_key: str | None
    text_content: str | None
    url: str | None
    uploaded_at: datetime


@dataclass
class SourceMetadata:
    name: str
    type: SourceType
    uploaded_at: str


class SourceConnector(Protocol):
    async def fetch(self, source: SourceRef) -> str: ...
    async def get_metadata(self, source: SourceRef) -> SourceMetadata: ...
    def supports(self, source_type: SourceType) -> bool: ...
