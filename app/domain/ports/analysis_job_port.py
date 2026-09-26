"""Port (интерфейс) для persistence-операций над AnalysisJob."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.domain.enums import AnalysisJobStatus

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.document import Document


@runtime_checkable
class AnalysisJobPort(Protocol):
    """Все методы, которые используют доменные сервисы.

    Concrete-реализация — AnalysisJobRepository в infrastructure/db/repositories.
    """

    async def get_by_id(self, job_id: uuid.UUID) -> "AnalysisJob | None": ...

    async def get_by_idempotency_key(
        self, document_id: uuid.UUID, idempotency_key: str
    ) -> "AnalysisJob | None": ...

    async def get_active_by_document_id(
        self, document_id: uuid.UUID
    ) -> "AnalysisJob | None": ...

    async def create_for_document(
        self, job: "AnalysisJob", document: "Document"
    ) -> "AnalysisJob": ...

    async def mark_dispatched(
        self, job: "AnalysisJob", document: "Document", task_id: str
    ) -> "AnalysisJob": ...

    async def mark_failed_queue_unavailable(
        self, job: "AnalysisJob", document: "Document", message: str | None
    ) -> "AnalysisJob": ...

    async def cancel(self, job: "AnalysisJob", document: "Document") -> "AnalysisJob": ...

    async def mark_processing_if_active(self, job_id: uuid.UUID) -> bool: ...

    async def update_status(
        self,
        job: "AnalysisJob",
        status: AnalysisJobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> "AnalysisJob": ...
