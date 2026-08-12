"""
Фиксация действий пользователя для журнала аудита: accept/reject правки и download документа.
Отдельный маленький сервис, а не метод внутри DocumentService/SuggestionService — это
cross-cutting concern, который не должен размывать ответственность основных сервисов.

Путь в репозитории: app/domain/services/audit_log_service.py
"""

import uuid

from app.infrastructure.db.models.audit_log import AuditLog
from app.infrastructure.db.models.enums import AuditAction
from app.infrastructure.db.repositories.audit_log_repository import AuditLogRepository


class AuditLogService:
    def __init__(self, audit_log_repository: AuditLogRepository):
        self._audit_logs = audit_log_repository

    async def log_download(self, user_id: uuid.UUID, document_id: uuid.UUID) -> AuditLog:
        entry = AuditLog(user_id=user_id, document_id=document_id, action=AuditAction.DOWNLOAD)
        return await self._audit_logs.create(entry)

    async def log_suggestion_decision(self, user_id: uuid.UUID, suggestion_id: uuid.UUID, action: AuditAction) -> AuditLog:
        entry = AuditLog(user_id=user_id, suggestion_id=suggestion_id, action=action)
        return await self._audit_logs.create(entry)
