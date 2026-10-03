"""Порт очереди фоновых задач анализа."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol


class AnalysisQueue(Protocol):
    async def enqueue(self, job_id: uuid.UUID, source_ids: Sequence[uuid.UUID]) -> str:
        """Поставить анализ в очередь и вернуть идентификатор задачи в очереди.

        Бросает исключение, если очередь недоступна.
        """
        ...

    async def revoke(self, task_id: str) -> None:
        """Отозвать задачу, если она ещё не начала выполняться."""
        ...
