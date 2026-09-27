"""
Схемы для дашборда (рабочее пространство).

GET /dashboard           — агрегаты + тренды виджетов
GET /documents/attention — топ-4 документа в awaiting_approval
GET /documents/recent    — 5 последних открытых
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


# ── GET /dashboard ──────────────────────────────────────────────────────────────────

class TrendPoint(BaseModel):
    """[дата, значение] для sparkline-графика."""
    date: str    # ISO 8601, например "2026-09-27"
    value: float


class DashboardResponse(BaseModel):
    """Агрегаты + sparkline-тренды экрана «Рабочее пространство».

    Текущее состояние (виджеты):
      total_documents         — документов всего
      awaiting_approval_count — ожидают проверки
      relevance_percent       — актуальность базы (ready / total × 100)

    Sparkline-тренды за 7 дней ([дата, значение]):
      total_trend     — общее кол-во документов на конец каждого дня
      awaiting_trend  — кол-во документов awaiting_approval в моменте
      relevance_trend — актуальность базы (%)

    Значения берутся из dashboard_snapshots; дни без снэпшота
    заполняются текущим значением.
    """
    # — текущее состояние
    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    relevance_percent: float

    # — sparkline-тренды
    total_trend: list[TrendPoint]
    awaiting_trend: list[TrendPoint]
    relevance_trend: list[TrendPoint]


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
