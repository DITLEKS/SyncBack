"""
Infrastructure enums — DB-level string enumerations.

FIX-review-7: DocumentStatus дополнен значениями ERROR и CANCELLED,
    которые присутствуют в DocumentStatusVO (domain) и используются
    в editor.py роутере (_STATUS_VIEW_MODE). Без этих значений PostgreSQL
    enum-тип отвергал запись статуса при попытке UPDATE documents SET status='error'.
    ВАЖНО: при применении требуется миграция:
      ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'error';
      ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'cancelled';
"""
import enum

from app.domain.value_objects import DocumentFormatVO as DocumentFormat  # noqa: F401


class UserRole(enum.StrEnum):
    ADMIN  = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class DocumentStatus(enum.StrEnum):
    DRAFT             = "draft"
    IN_PROGRESS       = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    READY             = "ready"
    # FIX-review-7: синхронизировано с DocumentStatusVO (domain/value_objects.py).
    # Без этих значений записать ERROR/CANCELLED в БД невозможно.
    ERROR             = "error"
    CANCELLED         = "cancelled"


class SourceType(enum.StrEnum):
    """После миграции 0018 в БД только 'file' и 'url'."""
    FILE = "file"
    URL  = "url"


class AnalysisJobStatus(enum.StrEnum):
    PENDING         = "pending"
    DISPATCHED      = "dispatched"
    PROCESSING      = "processing"
    SUCCESS         = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED          = "failed"
    CANCELLED       = "cancelled"


class ChangeType(enum.StrEnum):
    ADD    = "add"
    MODIFY = "modify"
    DELETE = "delete"


class SuggestionStatus(enum.StrEnum):
    PENDING  = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AuditAction(enum.StrEnum):
    ACCEPT          = "accept"
    REJECT          = "reject"
    BULK_ACCEPT     = "bulk_accept"
    BULK_REJECT     = "bulk_reject"      # синхронизировано с AuditActionVO
    FINALIZE        = "finalize"
    FINALIZE_REVIEW = "finalize_review"  # синхронизировано с AuditActionVO
    REOPEN          = "reopen"
    RESET           = "reset"            # синхронизировано с AuditActionVO
    DOWNLOAD        = "download"
