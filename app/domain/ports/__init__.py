"""Доменные порты (интерфейсы persistence-адаптеров).

Импортировать конкретные порты напрямую из их модулей:
    from app.domain.ports.project_port import ProjectPort
    from app.domain.ports.document_port import DocumentPort
    from app.domain.ports.suggestion_port import SuggestionPort
    from app.domain.ports.analysis_job_port import AnalysisJobPort
"""

from app.domain.ports.analysis_job_port import AnalysisJobPort
from app.domain.ports.document_port import DocumentPort
from app.domain.ports.project_port import ProjectPort
from app.domain.ports.suggestion_port import SuggestionPort

__all__ = [
    "AnalysisJobPort",
    "DocumentPort",
    "ProjectPort",
    "SuggestionPort",
]
