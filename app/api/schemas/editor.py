"""Схемы для роутера editor (aggregate + atomic review)."""
import uuid
from typing import List

from pydantic import BaseModel, Field

from app.api.schemas.analysis_job import AnalysisJobResponse
from app.api.schemas.document import DocumentContentResponse, DocumentResponse
from app.api.schemas.pagination import Page
from app.api.schemas.suggestion import SuggestionResponse


class EditorAggregateResponse(BaseModel):
    """P0-8: один HTTP-запрос вместо трёх при открытии редактора."""

    document: DocumentResponse
    content: DocumentContentResponse
    suggestions: Page[SuggestionResponse]
    current_analysis_job: AnalysisJobResponse | None = None
    review_version: int


# ---------------------------------------------------------------------------
# P0-2: AtomicReviewRequest — «Сохранить всё» без race condition
# ---------------------------------------------------------------------------

class AtomicReviewRequest(BaseModel):
    """Тело запроса PUT /editor/{document_id}/review.

    review_version — оптимистичная блокировка: клиент передаёт значение,
    полученное при последнем GET. Если к моменту PUT версия в БД изменилась,
    сервер возвращает 409 Conflict.
    """

    review_version: int = Field(
        ...,
        description="Версия ревью, известная клиенту (из EditorAggregateResponse.review_version)",
    )
    accepted_ids: List[uuid.UUID] = Field(
        default_factory=list,
        description="UUID правок, которые пользователь принял",
    )
    rejected_ids: List[uuid.UUID] = Field(
        default_factory=list,
        description="UUID правок, которые пользователь отклонил",
    )


class AtomicReviewResponse(BaseModel):
    """Ответ на PUT /editor/{document_id}/review."""

    review_version: int = Field(description="Новая версия ревью после применения изменений")
    accepted_count: int
    rejected_count: int
