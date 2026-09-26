"""Domain ports — абстракции persistence-слоя.

Сервисы в app/domain/services зависят только от этих Protocol-интерфейсов.
Сonkrete SQLAlchemy-репозитории живут в app/infrastructure и реализуют
порты структурно (duck typing + @runtime_checkable).
"""
from app.domain.ports.project_port import ProjectPort
from app.domain.ports.document_port import DocumentPort
from app.domain.ports.suggestion_port import SuggestionPort
from app.domain.ports.analysis_job_port import AnalysisJobPort

__all__ = [
    "ProjectPort",
    "DocumentPort",
    "SuggestionPort",
    "AnalysisJobPort",
]
