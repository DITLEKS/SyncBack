"""
Схема агрегированного ответа редактора документа.

GET /projects/{project_id}/documents/{document_id}/editor

Исправления P0 (#7, #8):
  #7 — добавлены view_mode (оригинал / правки / чистовик) и original_plain_text в
     EditorDocumentMeta / EditorAggregateResponse. Фронт не должен дополнительно
     опрашивать за исходным текстом.
  #8 — добавлен sources_is_editable: bool в EditorPermissions —
     фронт видит режим read-only для блока источников без повторного запроса.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.api.schemas.document import DocumentSectionResponse, SuggestionCounters
from app.api.schemas.suggestion import SuggestionResponse
from app.infrastructure.db.models.enums import DocumentStatus

# Три режима отображения, определённых в документации:
# • original  — исходный текст до правок
# • suggested — текущий текст + правки (inline diff)
# • clean     — чистовик: текст с принятыми правками
EditorViewMode = Literal["original", "suggested", "clean"]


class EditorDocumentMeta(BaseModel):
    """Mетаданные документа для редактора."""

    id: uuid.UUID
    title: str
    format: str
    status: DocumentStatus
    current_analysis_job_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    review_version: int
    # #7: текущий режим отображения, выводимый бэкендом по статусу:
    #   draft / in_progress                → "original"
    #   awaiting_approval                  → "suggested"
    #   ready                              → "clean"
    view_mode: EditorViewMode = "original"


class EditorContent(BaseModel):
    """Kонтент документа: plain_text + позиции секций."""

    plain_text: str
    sections: list[DocumentSectionResponse]


class EditorPermissions(BaseModel):
    """Доступные действия для текущего пользователя."""

    can_analyze: bool
    can_review: bool
    can_export: bool
    can_delete: bool
    # #8: режим редактирования источников. False, если анализ in_progress
    # или документ awaiting_approval (job активен).
    sources_is_editable: bool = True


class EditorAggregateResponse(BaseModel):
    """Полный агрегат для экрана редактора — один запрос вместо N+1.

    Поля:
    - document:          метаданные + статус + review_version + view_mode (#7)
    - content:           plain_text + секции (None, если парсинг не выполнялся)
    - original_content:  исходный plain_text без правок (#7, None если анализ не запускался)
    - suggestions:       список правок текущего анализа (пусто, если анализа нет)
    - counters:          pending/accepted/rejected/total
    - permissions:       что разрешено + sources_is_editable (#8)
    """

    document: EditorDocumentMeta
    content: EditorContent | None
    original_content: EditorContent | None  # #7: исходный текст до правок
    suggestions: list[SuggestionResponse]
    counters: SuggestionCounters
    permissions: EditorPermissions
