"""Схемы правок (suggestions): ответ редактору и единый PATCH статусов."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SuggestionSourceResponse(BaseModel):
    """Источник, на основании которого была создана правка."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    type: Literal["url", "file"]
    url: str | None = None

    model_config = {"from_attributes": True}


class SuggestionResponse(BaseModel):
    """Правка анализа в том виде, в каком её показывает редактор."""

    id: uuid.UUID
    document_id: uuid.UUID
    analysis_job_id: uuid.UUID
    sources: list[SuggestionSourceResponse] = Field(default_factory=list)
    section_ref: str | None = None
    change_type: Literal["add", "modify", "delete"]
    original_text: str | None = None
    suggested_text: str | None = None
    edited_text: str | None = Field(
        default=None,
        description=(
            "Текст, которым пользователь заменил suggested_text при принятии; None — без правки"
        ),
    )
    rationale: str | None = Field(default=None, description="Обоснование правки от модели")
    confidence_score: float | None = Field(default=None, description="Уверенность модели, 0–1")
    block_id: str | None = None
    start_offset: int | None = Field(
        default=None, description="Начало фрагмента в тексте документа, если известно"
    )
    end_offset: int | None = None
    status: Literal["pending", "accepted", "rejected"]
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PatchSuggestionsRequest(BaseModel):
    """Тело запроса для PATCH /suggestions."""

    ids: list[uuid.UUID] | None = None
    filter: Literal["pending", "decided", "all"] | None = None
    status: Literal["accepted", "rejected", "pending"]

    @model_validator(mode="after")
    def _exactly_one_selector(self) -> "PatchSuggestionsRequest":
        has_ids = bool(self.ids)
        has_filter = self.filter is not None
        if has_ids == has_filter:
            raise ValueError(
                "Укажите ровно одно из полей: 'ids' (список UUID) "
                'или \'filter\' ("pending"|"decided"|"all").'
            )
        return self


class PatchSuggestionsResponse(BaseModel):
    """Ответ PATCH /suggestions."""

    updated_count: int
    document_status: str | None = None
    review_version: int | None = None
