"""
Все перечисления схемы БД в одном месте.
"""

import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class DocumentFormat(str, enum.Enum):
    DOC = "doc"
    DOCX = "docx"
    TXT = "txt"
    MARKDOWN = "markdown"


class DocumentStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    ERROR = "error"


class SourceType(str, enum.Enum):
    FILE = "file"
    NOTE = "note"
    LINK = "link"


class AnalysisJobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


class ChangeType(str, enum.Enum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"


class SuggestionStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AuditAction(str, enum.Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    DOWNLOAD = "download"
