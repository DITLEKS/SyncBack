"""Доменные события: факты о предметной области, которые произошли и зафиксированы в БД."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.value_objects import DocumentStatusVO


@dataclass(frozen=True, slots=True)
class DocumentStatusChanged:
    """Документ перешёл в новый статус (анализ запущен, завершён, ревью закрыто и т.п.).

    owner_id нужен адресной доставке: событие видит только владелец проекта.
    """

    document_id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    status: DocumentStatusVO
    current_analysis_job_id: uuid.UUID | None


DomainEvent = DocumentStatusChanged
