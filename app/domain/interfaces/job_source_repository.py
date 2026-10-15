"""Порт репозитория состояния обработки источников внутри задачи анализа."""

from __future__ import annotations

import uuid
from typing import Protocol


class SourceRunRecord(Protocol):
    @property
    def id(self) -> uuid.UUID: ...


class IAnalysisJobSourceRepository(Protocol):
    async def ensure(self, job_id: uuid.UUID, source_id: uuid.UUID) -> SourceRunRecord:
        """Вернуть запись (job_id, source_id), создав её при отсутствии."""
        ...

    async def claim(
        self,
        job_id: uuid.UUID,
        source_id: uuid.UUID,
        lease_seconds: int,
        claim_token: uuid.UUID | None = None,
    ) -> bool:
        """Захватить источник перед вызовом LLM; False, если его держит другой воркер."""
        ...

    async def release(
        self,
        job_id: uuid.UUID,
        source_id: uuid.UUID,
        claim_token: uuid.UUID | None = None,
    ) -> None:
        """Снять захват; с claim_token — только если он принадлежит этому владельцу."""
        ...

    async def mark_completed(
        self,
        job_id: uuid.UUID,
        source_id: uuid.UUID,
        suggestions_count: int,
        claim_token: uuid.UUID | None = None,
    ) -> bool:
        """Пометить источник завершённым; False, если он уже завершён или захват потерян."""
        ...

    async def get_completed_count(self, job_id: uuid.UUID, source_id: uuid.UUID) -> int | None:
        """Число правок завершённого источника или None, если он не завершён."""
        ...
