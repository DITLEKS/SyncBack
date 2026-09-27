"""
IDashboardQueryService — порт read-model для дашборда.

L-C: DashboardRepository — это query-модель, а не агрегатный репозиторий.
Unit of Work управляет агрегатами; read-model’ы инжектируются
напрямую через FastAPI Depends.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import date


class IDashboardQueryService(ABC):
    """Read-only агрегаты дашборда. Реализация: SqlAlchemyDashboardQueryService."""

    @abstractmethod
    async def get_stats(self, owner_id: uuid.UUID) -> dict:
        """Агрегатная статистика: {total, awaiting, ready}."""

    @abstractmethod
    async def get_trends(
        self, owner_id: uuid.UUID, days: int = 7
    ) -> list[dict]:
        """Снэпшоты за `days` дней из dashboard_snapshots.

        Возвращает отсортированный по дате список:
          [{snapshot_date, total_count, awaiting_count, relevance_percent}]
        Дни без снэпшота в список не включаются —
        сервис заполняет их текущим значением.
        """

    @abstractmethod
    async def upsert_snapshot(
        self,
        owner_id: uuid.UUID,
        snapshot_date: date,
        total_count: int,
        awaiting_count: int,
        relevance_percent: float,
    ) -> None:
        """Сохранить снэпшот за день (ON CONFLICT DO UPDATE).

        Вызывается фоновым джобом.
        """

    @abstractmethod
    async def get_attention_documents(
        self, owner_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        """Топ-N документов AWAITING_APPROVAL по pending_suggestions DESC."""

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
