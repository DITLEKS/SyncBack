"""Pydantic-схемы для предложений."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SuggestionSourceResponse(BaseModel):
    """Источник, подтверждающий предложение."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    source_type: str
    url: str | None = None


class SuggestionResponse(BaseModel):
    """Предложение правки."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    analysis_job_id: uuid.UUID
    seq: int
    section_ref: str
    change_type: str
    original_text: str
    suggested_text: str
    rationale: str | None = None
    confidence: int | None = None
    status: str
    edited_text: str | None = None
    block_id: uuid.UUID | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    sources: list[SuggestionSourceResponse] = Field(default_factory=list)
    created_at: datetime
