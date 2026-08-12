"""
Импортируем все ORM-модели в одном месте, чтобы Base.metadata знала обо всех таблицах.
"""

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.audit_log import AuditLog
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.document_source import document_sources
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.models.user import User

__all__ = [
    "AnalysisJob",
    "AuditLog",
    "Document",
    "document_sources",
    "Project",
    "Source",
    "Suggestion",
    "User",
]
