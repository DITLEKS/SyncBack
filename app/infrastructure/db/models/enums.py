"""
Все перечисления схемы БД в одном месте.

ИСПРАВЛЕНО (UP042): классы переведены с `class X(str, enum.Enum)` на `enum.StrEnum`
(доступен с Python 3.11, requires-python = ">=3.12" в pyproject.toml). Проверено, что
это не меняет видимое поведение: везде, где эти enum сериализуются наружу
(SQLAlchemy через values_callable=... .value, Pydantic-схемы с полями типа str,
json.dumps в logging_setup.py), используется либо .value, либо тот факт, что сам
объект — уже валидный str (его "символьное" содержимое не меняется). Единственное
отличие — str(member)/f-string без .value теперь возвращает "admin" вместо
"UserRole.ADMIN"; в кодовой базе таких мест не найдено.
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
    UPLOADED = "uploaded"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    ERROR = "error"


class SourceType(enum.StrEnum):
    FILE = "file"
    NOTE = "note"
    LINK = "link"


class AnalysisJobStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


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
