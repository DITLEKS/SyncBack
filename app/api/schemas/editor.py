"""
Схема агрегированного ответа редактора документа.

GET /projects/{project_id}/documents/{document_id}/editor

Исправления P0 (#7, #8):
  #7 — добавлены view_mode (оригинал / правки / чистовик) и original_plain_text в
     EditorDocumentMeta / EditorAggregateResponse. Фронт не должен дополнительно
     опрашивать за исходным текстом.
  #8 — добавлен sources_is_editable: bool в EditorPermissions —
     фронт видит режим read-only для блока источников без повторного запроса.

API-1: добавлено поле suggestions_total в EditorAggregateResponse —
     общее число правок документа (не зависит от текущей страницы).
     counters.total = len(suggestions на странице); suggestions_total = полный итог.

API-5: добавлена схема ResetResponse — возвращается вместо 204 после POST /editor/reset,
     чтобы фронт обновил стор без дополнительного GET /editor.
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
    """Метаданные документа для редактора."""

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
    """Контент документа: plain_text + позиции секций."""

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
    - document:           метаданные + статус + review_version + view_mode (#7)
    - content:            plain_text + секции (None, если парсинг не выполнялся)
    - original_content:   исходный plain_text без правок (#7, None если анализ не запускался)
    - suggestions:        список правок текущей СТРАНИЦЫ
    - suggestions_total:  полное число правок документа (API-1) — используется
                          фронтом для построения пагинатора; НЕ зависит от
                          текущего suggestions_limit/suggestions_offset
    - counters:           pending/accepted/rejected/total (по странице)
    - permissions:        что разрешено + sources_is_editable (#8)
    """

    document: EditorDocumentMeta
    content: EditorContent | None
    original_content: EditorContent | None  # #7: исходный текст до правок
    suggestions: list[SuggestionResponse]
    # API-1: полный итог всех правок документа (не только текущей страницы)
    suggestions_total: int = 0
    counters: SuggestionCounters
    permissions: EditorPermissions


# ---------------------------------------------------------------------------
# API-5: схема ответа POST /editor/reset
# ---------------------------------------------------------------------------

class ResetResponse(BaseModel):
    """Ответ POST /editor/reset (API-5).

    Возвращается вместо 204, чтобы фронт мог обновить стор
    без дополнительного GET /editor.

    Поля:
    - document_id:             идентификатор документа
    - document_status:         новый статус (awaiting_approval)
    - review_version:          актуальная версия после сброса
    - suggestions_reset_count: число правок, переведённых в PENDING
    """
    document_id: uuid.UUID
    document_status: str
    review_version: int
    suggestions_reset_count: int
