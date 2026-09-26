"""
Схемы для дашборда (рабочее пространство).

#1 — GET /dashboard
#2 — GET /documents/attention
#3 — GET /documents/recent
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


# ── #1 Dashboard ──────────────────────────────────────────────────────────────

class DayActivity(BaseModel):
    date: str          # ISO 8601 date, e.g. "2026-09-14"
    opens: int         # кол-во уникальных открытий документов за день


class DashboardResponse(BaseModel):
    """Агрегаты для экрана «Рабочее пространство»."""
    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    # актуальность базы = ready / total * 100 (0 если total=0)
    relevance_percent: float
    # мини-график активности за последние 7 дней
    activity_last_7_days: list[DayActivity]


# ── #2 Attention ──────────────────────────────────────────────────────────────

class AttentionDocumentItem(BaseModel):
    id: uuid.UUID
    title: str
    project_id: uuid.UUID
    project_name: str
    # статус документа — всегда awaiting_approval, но фронт использует
    # для отображения цветного бейджа статуса на плашке
    status: str
    pending_suggestions: int
    updated_at: datetime


# ── #3 Recent ─────────────────────────────────────────────────────────────────

class RecentDocumentItem(BaseModel):
    id: uuid.UUID
    title: str
    project_id: uuid.UUID
    project_name: str
    status: str
    last_opened_at: datetime
    # счётчики для колонки «Изменений» (прогресс-бар resolved/total)
    suggestions_total: int = 0
    suggestions_resolved: int = 0   # accepted + rejected
