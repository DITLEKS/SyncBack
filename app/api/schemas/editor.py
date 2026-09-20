"""
Схема агрегированного ответа редактора документа (P0-8).

GET /projects/{project_id}/documents/{document_id}/editor
Возвращает одним запросом всё, что нужно экрану редактора:
- метаданные документа и статус
- исходный контент (plain_text + секции)
- список правок с их статусами
- счётчики и review_version для оптимистической блокировки
- права текущего пользователя на действия
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.document import DocumentSectionResponse, SuggestionCounters
from app.api.schemas.suggestion import SuggestionResponse
from app.infrastructure.db.models.enums import DocumentStatus


class EditorDocumentMeta(BaseModel):
    """Метаданные документа для редактора."""

    id: uuid.UUID
    title: str
    format: str
    status: DocumentStatus
    current_analysis_job_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    review_version: int


class EditorContent(BaseModel):
    """Контент документа: plain_text + позиции секций."""

    plain_text: str
    sections: list[DocumentSectionResponse]


class EditorPermissions(BaseModel):
    """Доступные действия для текущего пользователя."""

    can_analyze: bool
    can_review: bool
    can_export: bool
    can_delete: bool


class EditorAggregateResponse(BaseModel):
    """Полный агрегат для экрана редактора — один запрос вместо N+1.

    Поля:
    - document: метаданные + статус + review_version
    - content: plain_text + секции (None, если парсинг не выполнялся)
    - suggestions: список правок текущего анализа (пусто, если анализа нет)
    - counters: pending/accepted/rejected/total
    - permissions: что разрешено текущему пользователю
    """

    document: EditorDocumentMeta
    content: EditorContent | None
    suggestions: list[SuggestionResponse]
    counters: SuggestionCounters
    permissions: EditorPermissions
