"""Репозиторий журнала действий (accept/reject/download)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.audit_log import AuditLog


class AuditLogRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, entry: AuditLog) -> AuditLog:
        self._session.add(entry)
        await self._session.flush()
        await self._session.refresh(entry)
        return entry

    async def bulk_create(self, entries: list[AuditLog]) -> None:
        """Один flush для всех записей; commit управляется вызывающим кодом."""
        if not entries:
            return
        self._session.add_all(entries)
        await self._session.flush()
