"""
P0-3: добавлены поля block_id, start_offset, end_offset в SuggestionResponse.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


class SuggestionResponse(BaseModel):
    id: uuid.UUID
    analysis_job_id: uuid.UUID
    section_ref: str
    # P0-3: якоря правки
    block_id: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    change_type: str
    old_text: str | None
    new_text: str | None
    status: str
    source_reference: str | None
    confidence_score: float | None
    explanation: str | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


class BulkAcceptResponse(BaseModel):
    accepted_count: int
