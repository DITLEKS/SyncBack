"""Доменные перечисления SyncBack.

Это чистые бизнес-понятия, используемые доменными сервисами,
репозиториями и ORM-моделями без привязки к SQLAlchemy.
"""

import enum


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    USER = "user"


class DocumentFormat(enum.StrEnum):
    DOC = "doc"
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
    PARTIAL_SUCCESS = "partial_success"
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
