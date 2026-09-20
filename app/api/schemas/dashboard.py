"""
Схемы для дашборда (рабочее пространство).

#1 — GET /dashboard
#2 — GET /documents/attention
#3 — GET /documents/recent

refactor(#17): добавлен model_config = {"from_attributes": True} для
               моделей, которые будут валидированы из ORM-объектов.
refactor(#12): поле title переименовано в name для единообразия с ORM.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel


# ── #1 Dashboard ──────────────────────────────────────────────────────────────

class DayActivity(BaseModel):
    date: str          # ISO 8601 date, e.g. "2026-09-14"
    analyzed: int      # кол-во завершённых analysis_jobs за день
    approved: int      # кол-во finalize за день


class DashboardResponse(BaseModel):
    """Агрегаты для экрана «Рабочее пространство»."""

    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    # актуальность базы = ready / total * 100 (0 если total=0)
    relevance_percent: float
    # мини-график активности за последние 7 дней
    activity_last_7_days: list[DayActivity]

    model_config = {"from_attributes": True}


# ── #2 Attention ──────────────────────────────────────────────────────────────

class AttentionDocumentItem(BaseModel):
    id: uuid.UUID
    name: str          # #12: было title — переименовано для единообразия с ORM
    project_id: uuid.UUID
    project_name: str
    pending_suggestions: int
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── #3 Recent ─────────────────────────────────────────────────────────────────

class RecentDocumentItem(BaseModel):
    id: uuid.UUID
    name: str          # #12: было title — переименовано для единообразия с ORM
    project_id: uuid.UUID
    project_name: str
    status: str
    last_opened_at: datetime

    model_config = {"from_attributes": True}
