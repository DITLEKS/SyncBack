"""
Репозиторий журнала действий (accept/reject/download).

Путь в репозитории: app/infrastructure/db/repositories/audit_log_repository.py

H1: commit() удалён — транзакция фиксируется в get_db_session().
Аудит-запись всегда записывается в той же транзакции, что и бизнес-действие
(accept/reject suggestion), что даёт настоящую атомарность: либо оба изменения
сохранятся, либо оба откатятся.
"""

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
        """Один flush вместо N последовательных автофайлов.

        Не возвращает записи с id — audit_log не читается обратно в эндпоинтах.
        """
        if not entries:
            return
        for entry in entries:
            self._session.add(entry)
        await self._session.flush()
