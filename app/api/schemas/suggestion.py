"""
Схемы правок (suggestions).

P0-#13: BulkAcceptResponse расширен полями document_status и review_version,
        чтобы фронт не делал лишний GET /editor после bulk-accept.
RESET:  SuggestionResponse расширен nullable-полями decided_by / decided_at —
        после reset оба поля равны null, что сигнализирует фронту о сбросе.
PATCH:  PatchSuggestionsRequest/Response — единый bulk/single update статуса правок.
"""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class SuggestionResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    section_ref: str | None = None
    original_text: str
    suggested_text: str
    comment: str | None = None
    status: str
    # Nullable: None означает что решение ещё не принято (PENDING) или было сброшено.
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BulkAcceptResponse(BaseModel):
    accepted_count: int
    # P0-#13: статус документа после bulk-accept + актуальная версия ревью.
    document_status: str | None = None
    review_version: int | None = None


class BulkRejectResponse(BaseModel):
    rejected_count: int
    document_status: str | None = None
    review_version: int | None = None


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
                "или 'filter' (\"pending\"|\"decided\"|\"all\")."
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
