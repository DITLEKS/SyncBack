"""
Схемы правок (suggestions).

P0-#13: BulkAcceptResponse расширен полями document_status и review_version,
        чтобы фронт не делал лишний GET /editor после bulk-accept.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


class SuggestionResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    section_ref: str | None = None
    original_text: str
    suggested_text: str
    comment: str | None = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BulkAcceptResponse(BaseModel):
    accepted_count: int
    # P0-#13: статус документа после bulk-accept + актуальная версия ревью.
    # Позволяет фронту обновить UI без дополнительного запроса.
    document_status: str | None = None
    review_version: int | None = None
