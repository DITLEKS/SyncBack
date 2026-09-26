"""
Сервис аудит-лога.

Архитектурное правило: зависит только от IUnitOfWork.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.infrastructure.db.models.audit_log import AuditLog


class AuditLogService:
    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow

    async def log(
        self,
        document_id: uuid.UUID,
        action: str,
        performed_by: uuid.UUID,
        details: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            id=uuid.uuid4(),
            document_id=document_id,
            action=action,
            performed_by=performed_by,
            details=details or {},
            created_at=datetime.now(UTC),
        )
        async with self._uow:
            result = await self._uow.audit.create(entry)
            await self._uow.commit()
        return result

    async def list_for_document(
        self,
        document_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLog]:
        async with self._uow:
            return await self._uow.audit.list_for_document(
                document_id, limit=limit, offset=offset
            )
