"""Реэкспорт доменных enum-ов для обратной совместимости инфраструктуры.

DDD: перечисления являются доменными понятиями и определяются в
app.domain.enums. Этот модуль оставлен как thin wrapper, чтобы не ломать
существующие импорты в ORM/миграциях/адаптерах.
"""

from app.domain.enums import (
    AnalysisJobStatus,
    AuditAction,
    ChangeType,
    DocumentFormat,
    DocumentStatus,
    SourceType,
    SuggestionStatus,
    UserRole,
)

__all__ = [
    "UserRole",
    "DocumentFormat",
    "DocumentStatus",
    "SourceType",
    "AnalysisJobStatus",
    "ChangeType",
    "SuggestionStatus",
    "AuditAction",
]
