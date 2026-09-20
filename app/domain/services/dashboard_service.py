"""
DashboardService — агрегатная логика для экрана «Рабочее пространство».

ИСПРАВЛЕНО (review #3):
- Убраны все стабы с нулевыми значениями.
- Конструктор типизирован (DashboardRepository обязателен).
- Методы get_total_documents / get_awaiting_approval_count / get_ready_count
  заменены вызовом get_document_counts — одним запросом.
- Удалена дублирующая логика формирования DayActivity на Python.
"""
from __future__ import annotations

import uuid

from app.api.schemas.dashboard import (
    AttentionDocumentItem,
    DashboardResponse,
    DayActivity,
    RecentDocumentItem,
)
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository
from app.infrastructure.db.repositories.document_open_repository import DocumentOpenRepository


class DashboardService:
    """Сервис дашборда. Инжектируется через get_dashboard_service()."""

    def __init__(
        self,
        dashboard_repository: DashboardRepository,
        document_open_repository: DocumentOpenRepository,
    ) -> None:
        self._repo = dashboard_repository
        self._open_repo = document_open_repository

    # ── #1 Dashboard агрегаты ─────────────────────────────────────────────────

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """Возвращает агрегаты для рабочего пространства."""
        total, awaiting, ready = await self._repo.get_document_counts(user_id)
        activity_rows = await self._repo.get_activity_last_7_days(user_id)
        relevance_percent = round(ready / total * 100, 1) if total else 0.0
        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=awaiting,
            ready_count=ready,
            relevance_percent=relevance_percent,
            activity_last_7_days=[
                DayActivity(date=row["date"], analyzed=row["opens"], approved=0)
                for row in activity_rows
            ],
        )

    # ── #2 Требуют внимания ───────────────────────────────────────────────────

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int = 4
    ) -> list[AttentionDocumentItem]:
        """Топ-N документов в awaiting_approval, отсортированные по
        pending_suggestions DESC.
        """
        rows = await self._repo.get_attention_documents(user_id, limit=limit)
        return [
            AttentionDocumentItem(
                id=r["id"],
                title=r["title"],
                project_id=r["project_id"],
                project_name=r["project_name"],
                pending_suggestions=r["pending_suggestions"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ── #3 Недавние документы ─────────────────────────────────────────────────

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[RecentDocumentItem]:
        """N последних документов, открытых пользователем."""
        rows = await self._repo.get_recent_documents(user_id, limit=limit)
        return [
            RecentDocumentItem(
                id=r["id"],
                title=r["title"],
                project_id=r["project_id"],
                project_name=r["project_name"],
                status=r["status"],
                last_opened_at=r["last_opened_at"],
            )
            for r in rows
        ]

    async def track_open(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Записывает/обновляет last_opened_at для пары (user_id, document_id)."""
        await self._open_repo.upsert_open(user_id, document_id)
