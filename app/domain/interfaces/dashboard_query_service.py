"""
IDashboardQueryService — порт read-model для дашборда.

L-C: DashboardRepository — это query-модель, а не агрегатный репозиторий.
Unit of Work управляет агрегатами и транзакциями; read-model'и инжектируются
напрямую через FastAPI Depends, минуя IUnitOfWork.

Использование в роутере:
    async def get_dashboard(
        dashboard_svc: IDashboardQueryService = Depends(get_dashboard_query_service),
    ) -> ...
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod


class IDashboardQueryService(ABC):
    """Абстрактный read-model сервис дашборда.

    Все методы read-only (SELECT); транзакция не нужна.
    Реализация: SqlAlchemyDashboardQueryService.
    """

    @abstractmethod
    async def get_stats(self, owner_id: uuid.UUID) -> dict:
        """Агрегатная статистика документов пользователя: {total, awaiting, ready}."""

    @abstractmethod
    async def get_activity_last_7_days(self, owner_id: uuid.UUID) -> list[dict]:
        """Активность за 7 дней: [{date: str, opens: int}]."""

    @abstractmethod
    async def get_attention_documents(
        self, owner_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        """Топ-N документов AWAITING_APPROVAL, отсортированных по pending_suggestions DESC."""

    @abstractmethod
    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        """N последних открытых документов."""

    @abstractmethod
    async def upsert_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """Зафиксировать открытие документа (ON CONFLICT DO UPDATE)."""
