"""
Сервис аудит-лога.

MYPY-FIX: все методы получили явные аннотации возврата.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum

from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.infrastructure.db.models.audit_log import AuditLog


def _action_str(action: str | Enum) -> str:
    return action.value if isinstance(action, Enum) else action


class AuditLogService:
    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow

    async def log(
        self,
        document_id: uuid.UUID,
        action: str | Enum,
        performed_by: uuid.UUID,
        details: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            id=uuid.uuid4(),
            document_id=document_id,
            action=_action_str(action),
            performed_by=performed_by,
            details=details or {},
            created_at=datetime.now(UTC),
        )
        async with self._uow:
            result = await self._uow.audit.create(entry)
            await self._uow.commit()
        return result

    async def log_suggestion_decisions(
        self,
        document_id: uuid.UUID,
        performed_by: uuid.UUID,
        decisions: Sequence[tuple[uuid.UUID, str | Enum]],
    ) -> int:
        """Записывает решения по правкам одной транзакцией (один commit)."""
        if not decisions:
            return 0
        now = datetime.now(UTC)
        async with self._uow:
            for suggestion_id, action in decisions:
                await self._uow.audit.create(
                    AuditLog(
                        id=uuid.uuid4(),
                        document_id=document_id,
                        action=_action_str(action),
                        performed_by=performed_by,
                        details={"suggestion_id": str(suggestion_id)},
                        created_at=now,
                    )
                )
            await self._uow.commit()
        return len(decisions)

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
