"""
Доменные перечисления.

Все enum'ы живут здесь — в доменном слое. Они не зависят
ни от SQLAlchemy, ни от FastAPI, ни от каких-либо других фреймворков.

app/infrastructure/db/models/enums.py содержит только re-export этих
классов, чтобы не ломать существующие пути импорта в ORM-моделях,
репозиториях и API-схемах.
"""

import enum


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    USER = "user"


class DocumentFormat(enum.StrEnum):
    # .doc (OLE2 / Word 97-2003) исключён из допустимых форматов загрузки:
    # python-docx поддерживает только .docx (OpenXML). Значение оставлено
    # в enum для обратной совместимости с уже сохранёнными записями в БД,
    # но DocumentParserRegistry выбрасывает UnsupportedFormatError при
    # попытке распарсить такой файл.
    DOC = "doc"  # не поддерживается парсером — только для legacy-совместимости
    DOCX = "docx"
    TXT = "txt"
    MARKDOWN = "markdown"


class DocumentStatus(enum.StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"


class SourceType(enum.StrEnum):
    FILE = "file"
    NOTE = "note"
    LINK = "link"


class AnalysisJobStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"  # финализация с частичным результатом
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChangeType(enum.StrEnum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"


class SuggestionStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AuditAction(enum.StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    DOWNLOAD = "download"
