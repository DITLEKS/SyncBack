"""Port (интерфейс) для persistence-операций над Suggestion."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.domain.enums import SuggestionStatus
from app.infrastructure.db.repositories.suggestion_repository import SuggestionCounts

if TYPE_CHECKING:
    from app.infrastructure.db.models.suggestion import Suggestion


@runtime_checkable
class SuggestionPort(Protocol):
    """Все методы, которые используют доменные сервисы.

    Concrete-реализация — SuggestionRepository в infrastructure/db/repositories.
    """

    async def bulk_create(self, suggestions: "list[Suggestion]") -> "list[Suggestion]": ...

    async def get_by_id(self, suggestion_id: uuid.UUID) -> "Suggestion | None": ...

    async def list_by_analysis_job(
        self, analysis_job_id: uuid.UUID, limit: int, offset: int
    ) -> "list[Suggestion]": ...

    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int: ...

    async def count_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatus
    ) -> int: ...

    async def count_by_analysis_job_stats(
        self, analysis_job_id: uuid.UUID
    ) -> SuggestionCounts:
        """Возвращает (accepted, rejected, pending) одним COUNT-запросом."""
        ...

    async def list_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatus
    ) -> "list[Suggestion]": ...

    async def update_status(
        self,
        suggestion: "Suggestion",
        status: SuggestionStatus,
        decided_by: uuid.UUID,
    ) -> "Suggestion | None": ...

    async def bulk_update_status(
        self,
        suggestion_ids: list[uuid.UUID],
        analysis_job_id: uuid.UUID,
        status: SuggestionStatus,
        decided_by: uuid.UUID,
    ) -> "list[Suggestion]":
        """Обновляет конкретные id IN (...) с скоупом по analysis_job_id.

        analysis_job_id обязателен — защищает от мутации правок
        чужого документа при передаче произвольных UUID.
        """
        ...

    async def bulk_update_all_pending(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatus,
        decided_by: uuid.UUID,
    ) -> "list[Suggestion]":
        """Обновляет все pending-правки job одним UPDATE без промежуточного SELECT."""
        ...
