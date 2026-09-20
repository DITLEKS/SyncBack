"""
DashboardService — агрегатная логика для экрана «Рабочее пространство».

DDD-соответствие (#4):
  Сервис не импортирует API-схемы (app.api.*). Возвращает внутренние
  dataclass-объекты DashboardData / AttentionItem / RecentItem.
  Маппинг в Pydantic-схемы выполняется в роутере.

Методы:
  get_dashboard(user_id)              → DashboardData
  get_attention_documents(user_id)    → list[AttentionItem]
  get_recent_documents(user_id)       → list[RecentItem]
  track_open(user_id, document_id)    → None
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository
    from app.infrastructure.db.repositories.document_open_repository import DocumentOpenRepository


# ── Доменные объекты (не Pydantic, не ORM) ────────────────────────────────────

@dataclass
class DayActivityData:
    date: str   # ISO 8601, e.g. "2026-09-14"
    analyzed: int = 0
    approved: int = 0


@dataclass
class DashboardData:
    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    relevance_percent: float
    activity_last_7_days: list[DayActivityData] = field(default_factory=list)


@dataclass
class AttentionItem:
    id: uuid.UUID
    name: str
    project_id: uuid.UUID
    project_name: str
    pending_suggestions: int
    updated_at: object  # datetime, но избегаем импорта datetime в DDD-слое


@dataclass
class RecentItem:
    id: uuid.UUID
    name: str
    project_id: uuid.UUID
    project_name: str
    status: str
    last_opened_at: object  # datetime


# ── Сервис ────────────────────────────────────────────────────────────────────

class DashboardService:
    """Сервис дашборда. Инжектируется через get_dashboard_service()."""

    def __init__(
        self,
        dashboard_repository: "DashboardRepository",
        document_open_repository: "DocumentOpenRepository",
    ) -> None:
        self._repo = dashboard_repository
        self._open_repo = document_open_repository

    # ── #1 Dashboard агрегаты ─────────────────────────────────────────────────

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardData:
        """Возвращает агрегаты для рабочего пространства."""
        return await self._repo.get_dashboard_aggregates(user_id)

    # ── #2 Требуют внимания ───────────────────────────────────────────────────

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int = 4
    ) -> list[AttentionItem]:
        """Топ-N документов в awaiting_approval, по pending_suggestions DESC."""
        return await self._repo.get_attention_documents(user_id, limit=limit)

    # ── #3 Недавние документы ─────────────────────────────────────────────────

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[RecentItem]:
        """N последних документов, открытых пользователем."""
        return await self._open_repo.get_recent_for_user(user_id, limit=limit)

    async def track_open(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Записывает/обновляет last_opened_at для пары (user_id, document_id)."""
        await self._open_repo.upsert_open(user_id, document_id)

    # ── Вспомогательный метод для stub (тесты / стартовый режим) ─────────────

    @staticmethod
    def make_empty_dashboard() -> DashboardData:
        """Возвращает нулевой дашборд — для тестов и фолбэка."""
        today = date.today()
        return DashboardData(
            total_documents=0,
            awaiting_approval_count=0,
            ready_count=0,
            relevance_percent=0.0,
            activity_last_7_days=[
                DayActivityData(date=(today - timedelta(days=i)).isoformat())
                for i in range(6, -1, -1)
            ],
        )
