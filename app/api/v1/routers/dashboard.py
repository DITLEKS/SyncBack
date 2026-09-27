"""
Дашборд / рабочее пространство.

GET  /me/dashboard              — агрегаты + статистика виджетов (единый endpoint)
GET  /me/documents/attention    — топ-4 документа в awaiting_approval
GET  /me/documents/recent       — 5 последних открытых
POST /projects/{id}/documents/{id}/open  — трекинг открытия документа

GET /dashboard/stats удалён — его данные вошли в GET /me/dashboard (OPT-D1).
"""
import uuid

from fastapi import APIRouter, Depends, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.dashboard import (
    AttentionDocumentItem,
    DashboardResponse,
    RecentDocumentItem,
)
from app.core.dependencies import get_dashboard_service
from app.domain.services.dashboard_service import DashboardService
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/me", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> DashboardResponse:
    """Агрегаты + статистика виджетов рабочего пространства.

    Возвращает в одном ответе всё, что раньше было разнесено между
    GET /dashboard и GET /dashboard/stats.
    """
    return await svc.get_dashboard(current_user.id)


@router.get("/documents/attention", response_model=list[AttentionDocumentItem])
async def get_attention_documents(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> list[AttentionDocumentItem]:
    """Топ-4 документа со статусом awaiting_approval по кол-ву pending-правок."""
    return await svc.get_attention_documents(current_user.id, limit=4)


@router.get("/documents/recent", response_model=list[RecentDocumentItem])
async def get_recent_documents(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> list[RecentDocumentItem]:
    """5 последних документов, открытых текущим пользователем."""
    return await svc.get_recent_documents(current_user.id, limit=5)


@router.post(
    "/projects/{project_id}/documents/{document_id}/open",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["documents"],
    prefix="",  # этот маршрут не входит в /me — он на уровне /api/v1
)
async def track_document_open(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_allowed_project),
    svc: DashboardService = Depends(get_dashboard_service),
) -> None:
    """Трекинг открытия документа. Обновляет last_opened_at.

    OPT-D8: проект проверяется через get_allowed_project.
    """
    await svc.track_open(current_user.id, document_id, project_id=project.id)
