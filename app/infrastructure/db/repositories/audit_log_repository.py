"""
SQLAlchemy-адаптер для AuditLog.

Правило: НИКАКИХ session.commit() здесь.

M-NEW-3: create() переходит на фабричный паттерн: принимает параметры,
а не готовый ORM-инстанс AuditLog.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import IAuditLogRepository

if TYPE_CHECKING:
    from app.infrastructure.db.models.audit_log import AuditLog


class AuditLogRepository(IAuditLogRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID | None,
        action: str,
        details: Any | None = None,
    ) -> "AuditLog":
        """M-NEW-3: фабричный метод — сервис не импортирует AuditLog ORM."""
        from app.infrastructure.db.models.audit_log import AuditLog as M
        entry = M(
            document_id=document_id,
            user_id=user_id,
            action=action,
            details=details,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_for_document(
        self,
        document_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> "list[AuditLog]":
        from app.infrastructure.db.models.audit_log import AuditLog as M
        result = await self._session.execute(
            select(M)
            .where(M.document_id == document_id)
            .order_by(M.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
