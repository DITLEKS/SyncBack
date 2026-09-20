"""
Схема агрегированного ответа редактора документа.

GET /projects/{project_id}/documents/{document_id}/editor

Исправления P0 (#7, #8):
  #7 — добавлены view_mode (оригинал / правки / чистовик) и original_content в
     EditorDocumentMeta / EditorAggregateResponse.
  #8 — добавлен sources_is_editable: bool в EditorPermissions.

refactor(#5): title→name в EditorDocumentMeta для единообразия с ORM.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.api.schemas.document import DocumentSectionResponse, SuggestionCounters
from app.api.schemas.suggestion import SuggestionResponse
from app.infrastructure.db.models.enums import DocumentStatus

# Три режима отображения:
# • original  — исходный текст до правок
# • suggested — текущий текст + правки (inline diff)
# • clean     — чистовик: текст с принятыми правками
EditorViewMode = Literal["original", "suggested", "clean"]


class EditorDocumentMeta(BaseModel):
    """Метаданные документа для редактора."""

    id: uuid.UUID
    name: str          # #5: было title — исправлено для единообразия с ORM и DocumentResponse
    format: str
    status: DocumentStatus
    current_analysis_job_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    review_version: int
    # #7: view_mode вычисляется в DocumentService.resolve_view_mode()
    view_mode: EditorViewMode = "original"


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
    # #8: False если анализ in_progress или документ awaiting_approval.
    sources_is_editable: bool = True


class EditorAggregateResponse(BaseModel):
    """Полный агрегат для экрана редактора — один запрос вместо N+1.

    Поля:
    - document:          метаданные + статус + review_version + view_mode (#7)
    - content:           plain_text + секции (None если парсинг не выполнялся)
    - original_content:  исходный plain_text до правок (#7, None если анализ не запускался)
    - suggestions:       список правок текущего анализа
    - counters:          pending/accepted/rejected/total — считается одним Counter-проходом
    - permissions:       что разрешено + sources_is_editable (#8)
    """

    document: EditorDocumentMeta
    content: EditorContent | None
    original_content: EditorContent | None
    suggestions: list[SuggestionResponse]
    counters: SuggestionCounters
    permissions: EditorPermissions
