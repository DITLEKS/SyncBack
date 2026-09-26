"""Port (интерфейс) для dashboard-агрегатов."""
from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable


@runtime_checkable
class DashboardPort(Protocol):
    """Все методы, которые нужны DashboardService.

    Concrete-реализация — DashboardRepository в infrastructure/db/repositories.
    """

    async def get_stats(self, user_id: uuid.UUID) -> dict: ...
    """Возвращает dict с ключами: total, awaiting, ready."""

    async def get_activity_last_7_days(
        self, user_id: uuid.UUID
    ) -> list[dict]: ...
    """Список dict с ключами: date (str ISO 8601), analyzed (int), approved (int)."""

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int
    ) -> list[dict]: ...

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int
    ) -> list[dict]: ...

    async def upsert_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None: ...
