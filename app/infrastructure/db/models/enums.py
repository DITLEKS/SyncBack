"""
Re-export доменных перечислений.

Источник истины — app.domain.enums.
Этот модуль оставлен для обратной совместимости: ORM-модели, репозитории
и API-схемы продолжают импортировать из app.infrastructure.db.models.enums
без каких-либо изменений.

ПРИМЕЧАНИЕ (UP042): классы используют enum.StrEnum (Python ≥ 3.11).
Поведение не изменилось: str(member) возвращает строковое значение
(например, "admin"), а не "UserRole.ADMIN".
"""

from app.domain.enums import (  # noqa: F401
    AuditAction,
    AnalysisJobStatus,
    ChangeType,
    DocumentFormat,
    DocumentStatus,
    SourceType,
    SuggestionStatus,
    UserRole,
)

__all__ = [
    "AuditAction",
    "AnalysisJobStatus",
    "ChangeType",
    "DocumentFormat",
    "DocumentStatus",
    "SourceType",
    "SuggestionStatus",
    "UserRole",
]
