"""
Репозиторий журнала действий (accept/reject/download).

Путь в репозитории: app/infrastructure/db/repositories/audit_log_repository.py
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.audit_log import AuditLog


class AuditLogRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, entry: AuditLog) -> AuditLog:
        self._session.add(entry)
        await self._session.commit()
        await self._session.refresh(entry)
        return entry
