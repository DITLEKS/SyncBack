"""
Схемы для дашборда (рабочее пространство).

GET /dashboard           — агрегаты рабочего пространства
GET /documents/attention — топ-4 документа в awaiting_approval
GET /documents/recent    — 5 последних открытых
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


# ── GET /dashboard ──────────────────────────────────────────────────────────────────

class DayActivity(BaseModel):
    date: str   # ISO 8601 date, например "2026-09-14"
    opens: int  # кол-во уникальных открытий документов за день


class DashboardResponse(BaseModel):
    """Агрегаты экрана «Рабочее пространство».

    Соответствует виджетам UI:
      - Документов всего  → total_documents
      - Ожидают проверки → awaiting_approval_count
      - Актуальность базы → relevance_percent (ready / total × 100)
      - Мини-график активности → activity_last_7_days
    """
    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    relevance_percent: float
    activity_last_7_days: list[DayActivity]


# ── GET /documents/attention ──────────────────────────────────────────────────────

class AttentionDocumentItem(BaseModel):
    """Документы блока «Требуют внимания».

    status не возвращается: все документы здесь по контракту
    находятся в AWAITING_APPROVAL.
    """
    id: uuid.UUID
    title: str
    project_id: uuid.UUID
    project_name: str
    pending_suggestions: int
    updated_at: datetime


# ── GET /documents/recent ─────────────────────────────────────────────────────────────

class RecentDocumentItem(BaseModel):
    id: uuid.UUID
    title: str
    project_id: uuid.UUID
    project_name: str
    status: str
    last_opened_at: datetime
    suggestions_total: int = 0
    suggestions_resolved: int = 0   # accepted + rejected
