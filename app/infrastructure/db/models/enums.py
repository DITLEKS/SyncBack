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

LOW (DocumentFormat): класс перенесён в domain/value_objects.py как DocumentFormatVO.
Здесь оставлен только реэкспорт-алиас для обратной совместимости:
  - SQLAlchemy Enum(DocumentFormat, name="document_format") продолжает работать;
  - существующие миграции Alembic не затронуты (значения в БД не меняются);
  - весь код, который импортировал DocumentFormat из enums, продолжает работать
    без изменений благодаря алиасу.
"""

import enum

# ---------------------------------------------------------------------------
# LOW: DocumentFormat перенесён в domain — реэкспорт для совместимости
# ---------------------------------------------------------------------------
from app.domain.value_objects import DocumentFormatVO as DocumentFormat  # noqa: F401


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    USER = "user"


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
    PARTIAL_SUCCESS = "partial_success"  # P0-7: финализация с частичным результатом
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
