"""
Схемы для дашборда (рабочее пространство).

#1 — GET /dashboard
#2 — GET /documents/attention
#3 — GET /documents/recent
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


# ── Вспомогательные типы ───────────────────────────────────────────────────────────

class DayActivity(BaseModel):
    date: str          # ISO 8601 date, например "2026-09-14"
    opens: int         # кол-во уникальных открытий документов за день


class DocumentsByStatus(BaseModel):
    """Количество документов по каждому статусу (для pie/bar-виджета)."""
    draft: int = 0
    in_progress: int = 0
    awaiting_approval: int = 0
    ready: int = 0
    failed: int = 0


# ── #1 DashboardResponse ──────────────────────────────────────────────────────────

class DashboardResponse(BaseModel):
    """Полный ответ GET /dashboard.

    Содержит базовые агрегаты рабочего пространства +
    расширенную статистику виджетов (бывший /dashboard/stats, удалён).

    Поля статистики:
      saved_hours       — время (ч), сэкономленное авто-правками.
                          Расчёт: accepted_count × 3 мин / 60.
                          Показывается в виджете «Сэкономлено X часов».
      approved_percent  — доля принятых правок от всех решённых (0–100).
    """
    # — базовые агрегаты
    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    relevance_percent: float          # ready / total × 100
    activity_last_7_days: list[DayActivity]

    # — статистика виджетов
    saved_hours: float = 0.0
    approved_percent: float = 0.0     # 0.0 – 100.0
    documents_by_status: DocumentsByStatus = DocumentsByStatus()
    total_suggestions: int = 0
    accepted_count: int = 0
    rejected_count: int = 0


# ── #2 Attention ───────────────────────────────────────────────────────────────

class AttentionDocumentItem(BaseModel):
    """Документы блока «Требует внимания».

    status не возвращается: все документы здесь по контракту
    находятся в AWAITING_APPROVAL.
    """
    id: uuid.UUID
    title: str
    project_id: uuid.UUID
    project_name: str
    pending_suggestions: int
    updated_at: datetime


# ── #3 Recent ─────────────────────────────────────────────────────────────────────

class RecentDocumentItem(BaseModel):
    id: uuid.UUID
    title: str
    project_id: uuid.UUID
    project_name: str
    status: str
    last_opened_at: datetime
    suggestions_total: int = 0
    suggestions_resolved: int = 0   # accepted + rejected
