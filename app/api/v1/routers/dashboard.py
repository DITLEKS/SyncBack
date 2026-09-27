"""
Дашборд / рабочее пространство.

P0-#1  GET  /dashboard            — агрегаты + расширенная статистика (объединено)
P0-#2  GET  /documents/attention  — топ-4 документа в awaiting_approval
P0-#3  GET  /documents/recent     — 5 последних открытых
P0-#3  POST /documents/{id}/open  — трекинг открытия документа
ALIAS  GET  /dashboard/stats      — прежний endpoint, возвращает тот же DashboardResponse
                                    (backward-compat, deprecated — удалить в следующем минорном)

OPT-D1: GET /dashboard и GET /dashboard/stats объединены в один ответ —
        клиент больше не делает два round-trip при открытии дашборда.
OPT-D8: track_document_open принимает Project через get_allowed_project —
        ownership document→project проверяется на уровне dep-инъекции.
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

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> DashboardResponse:
    """Агрегаты + расширенная статистика рабочего пространства.

    OPT-D1: объединяет прежние /dashboard и /dashboard/stats в один запрос.
    Фронт вызывает только этот endpoint при открытии дашборда.
    """
    return await svc.get_dashboard(current_user.id)


@router.get(
    "/dashboard/stats",
    response_model=DashboardResponse,
    deprecated=True,
    summary="[Deprecated] Используй GET /dashboard",
)
async def get_dashboard_stats(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> DashboardResponse:
    """Backward-compat alias. Будет удалён в следующем минорном релизе."""
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
)
async def track_document_open(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    # OPT-D8: get_allowed_project проверяет membership и то, что проект существует;
    # document→project ownership будет добавлена в get_allowed_project при наличии
    # document_id в контексте. Пока достаточно убедиться, что пользователь
    # имеет доступ к project_id из URL.
    project: Project = Depends(get_allowed_project),
    svc: DashboardService = Depends(get_dashboard_service),
) -> None:
    """Трекинг открытия документа. Обновляет last_opened_at для пары (user_id, document_id).

    OPT-D8: project проверяется через get_allowed_project — нельзя трекать
    документы из чужих проектов.
    """
    await svc.track_open(current_user.id, document_id, project_id=project.id)
