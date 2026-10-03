"""SQLAlchemy-адаптер для AuditLog. Фиксация транзакции — на стороне UoW."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
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
        suggestion_id: uuid.UUID | None = None,
        details: Any | None = None,
    ) -> AuditLog:
        from app.infrastructure.db.models.audit_log import AuditLog as M

        entry = M(
            document_id=document_id,
            user_id=user_id,
            action=action,
            suggestion_id=suggestion_id,
            details=details,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def create_many(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID | None,
        action: str,
        suggestion_ids: Sequence[uuid.UUID],
    ) -> int:
        from app.infrastructure.db.models.audit_log import AuditLog as M

        if not suggestion_ids:
            return 0
        self._session.add_all(
            M(document_id=document_id, user_id=user_id, action=action, suggestion_id=sid)
            for sid in suggestion_ids
        )
        await self._session.flush()
        return len(suggestion_ids)

    async def list_for_document(
        self,
        document_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> list[AuditLog]:
        from app.infrastructure.db.models.audit_log import AuditLog as M

        result = await self._session.execute(
            select(M)
            .where(M.document_id == document_id)
            .order_by(M.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
