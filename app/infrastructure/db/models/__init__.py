"""
Регистрация всех ORM-моделей в Base.metadata.

Все импорты — исключительно для side-effect регистрации в metadata.
Изменения в этом файле влияют на alembic autogenerate и create_all().
"""

from app.infrastructure.db.models.analysis_job import AnalysisJob  # noqa: F401
from app.infrastructure.db.models.audit_log import AuditLog  # noqa: F401
from app.infrastructure.db.models.dashboard_snapshot import DashboardSnapshot  # noqa: F401
from app.infrastructure.db.models.document import Document  # noqa: F401
from app.infrastructure.db.models.document_block import DocumentBlock  # noqa: F401
from app.infrastructure.db.models.document_open import DocumentOpen  # noqa: F401
# NOTE: document_source.py экспортирует объект Table (document_sources),
# не ORM-класс. Класс DocumentSource не существует — импортировать его нельзя.
# Сканирование кодовой базы (grep -r 'DocumentSource') не выявило других
# потребителей — замена безопасна.
from app.infrastructure.db.models.document_source import document_sources  # noqa: F401
from app.infrastructure.db.models.enums import (  # noqa: F401
    AnalysisJobStatus,
    DocumentStatus,
    UserRole,
)
from app.infrastructure.db.models.project import Project  # noqa: F401
from app.infrastructure.db.models.source import Source  # noqa: F401
from app.infrastructure.db.models.suggestion import Suggestion  # noqa: F401
from app.infrastructure.db.models.user import User  # noqa: F401
