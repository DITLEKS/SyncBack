"""
DashboardService — агрегатная логика для экрана «Рабочее пространство».

Не выполняет прямых SQL-запросов: делегирует репозиториям.
В текущей реализации содержит заглушки (stub), которые нужно
заменить реальными запросами при добавлении DashboardRepository.

Методы:
  get_dashboard(user_id)              → DashboardResponse
  get_attention_documents(user_id)    → list[AttentionDocumentItem]
  get_recent_documents(user_id)       → list[RecentDocumentItem]
  track_open(user_id, document_id)    → None
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.api.schemas.dashboard import (
    AttentionDocumentItem,
    DashboardResponse,
    DayActivity,
    RecentDocumentItem,
)


class DashboardService:
    """Сервис дашборда. Инжектируется через get_dashboard_service().

    Аргументы конструктора будут расширены при добавлении
    DashboardRepository и DocumentOpenRepository.
    """

    def __init__(self, dashboard_repository=None, document_open_repository=None) -> None:
        self._repo = dashboard_repository
        self._open_repo = document_open_repository

    # ── #1 Dashboard агрегаты ─────────────────────────────────────────────────

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """Возвращает агрегаты для рабочего пространства.

        TODO: заменить заглушки реальными запросами через DashboardRepository.
        """
        if self._repo is not None:
            return await self._repo.get_dashboard_aggregates(user_id)

        # Stub — возвращает нулевые значения до появления репозитория
        return DashboardResponse(
            total_documents=0,
            awaiting_approval_count=0,
            ready_count=0,
            relevance_percent=0.0,
            activity_last_7_days=[
                DayActivity(
                    date=(date.today() - timedelta(days=i)).isoformat(),
                    analyzed=0,
                    approved=0,
                )
                for i in range(6, -1, -1)
            ],
        )

    # ── #2 Требуют внимания ───────────────────────────────────────────────────

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int = 4
    ) -> list[AttentionDocumentItem]:
        """Топ-N документов в awaiting_approval, отсортированные по
        pending_suggestions DESC.

        TODO: заменить заглушку реальным запросом.
        """
        if self._repo is not None:
            return await self._repo.get_attention_documents(user_id, limit=limit)
        return []

    # ── #3 Недавние документы ─────────────────────────────────────────────────

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[RecentDocumentItem]:
        """N последних документов, открытых пользователем.

        TODO: заменить заглушку реальным запросом.
        """
        if self._open_repo is not None:
            return await self._open_repo.get_recent_for_user(user_id, limit=limit)
        return []

    async def track_open(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Записывает/обновляет last_opened_at для пары (user_id, document_id).

        TODO: реализовать через DocumentOpenRepository → таблица document_opens.
        """
        if self._open_repo is not None:
            await self._open_repo.upsert_open(user_id, document_id)
