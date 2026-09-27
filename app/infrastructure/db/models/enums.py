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
