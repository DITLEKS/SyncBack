"""
SQLAlchemy-адаптер для AuditLog.

Правило: НИКАКИХ session.commit() здесь.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import IAuditLogRepository
from app.infrastructure.db.models.audit_log import AuditLog


class AuditLogRepository(IAuditLogRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, entry: AuditLog) -> AuditLog:
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_for_document(
        self,
        document_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> list[AuditLog]:
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.document_id == document_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
