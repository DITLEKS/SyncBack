"""Схемы правок (suggestions): ответ редактору и единый PATCH статусов."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SuggestionResponse(BaseModel):
    """Правка анализа в том виде, в каком её показывает редактор."""

    id: uuid.UUID
    document_id: uuid.UUID
    analysis_job_id: uuid.UUID
    section_ref: str | None = None
    change_type: Literal["add", "modify", "delete"]
    # У add нет исходного текста, у delete — предлагаемого.
    original_text: str | None = None
    suggested_text: str | None = None
    rationale: str | None = Field(default=None, description="Обоснование правки от модели")
    confidence_score: float | None = Field(default=None, description="Уверенность модели, 0–1")
    block_id: str | None = None
    start_offset: int | None = Field(
        default=None, description="Начало фрагмента в тексте документа, если известно"
    )
    end_offset: int | None = None
    status: Literal["pending", "accepted", "rejected"]
    # None — решение ещё не принято или сброшено.
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# PATCH /suggestions — единый эндпоинт обновления статуса правок
# ---------------------------------------------------------------------------


class PatchSuggestionsRequest(BaseModel):
    """Тело запроса для PATCH /suggestions.

    Ровно одно из полей ids / filter обязательно.

    ids    — список UUID: точечное обновление конкретных правок.
    filter — предустановленный фильтр:
               "pending"  — все правки в статусе pending
               "decided"  — все accepted + rejected (для массового reset)
               "all"      — все правки документа
    status — целевой статус: "accepted" | "rejected" | "pending"
             "pending" = reset (сброс решения, аналог POST /{id}/reset).
    """

    ids: list[uuid.UUID] | None = None
    filter: Literal["pending", "decided", "all"] | None = None
    status: Literal["accepted", "rejected", "pending"]

    @model_validator(mode="after")
    def _exactly_one_selector(self) -> "PatchSuggestionsRequest":
        has_ids = bool(self.ids)
        has_filter = self.filter is not None
        if has_ids == has_filter:  # оба True или оба False
            raise ValueError(
                "Укажите ровно одно из полей: 'ids' (список UUID) "
                'или \'filter\' ("pending"|"decided"|"all").'
            )
        return self


class PatchSuggestionsResponse(BaseModel):
    """Ответ PATCH /suggestions.

    updated_count   — количество фактически изменённых правок.
    document_status — новый статус документа (может измениться при finalize).
    review_version  — актуальная версия ревью для optimistic locking.
    """

    updated_count: int
    document_status: str | None = None
    review_version: int | None = None
