"""
Дашборд / рабочее пространство.

P0-#1  GET  /dashboard            — агрегаты
P0-#2  GET  /documents/attention  — топ-4 документа awaiting_approval
P0-#3  GET  /documents/recent     — 5 последних открытых
P0-#3  POST /projects/{project_id}/documents/{document_id}/open — трекинг

refactor(#3):  track_document_open защищён через get_allowed_project
               (проверяет принадлежность документа пользователю).
refactor(#4):  маппинг DashboardData → DashboardResponse выполнен здесь,
               а не в сервисе (DDD: сервис не знает о схемах API).
refactor(#11): удалены неиспользуемые импорты date/timedelta/timezone.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.dashboard import (
    AttentionDocumentItem,
    DashboardResponse,
    DayActivity,
    RecentDocumentItem,
)
from app.core.dependencies import get_dashboard_service
from app.domain.services.dashboard_service import (
    AttentionItem,
    DashboardData,
    DashboardService,
    RecentItem,
)
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

router = APIRouter(tags=["dashboard"])


# ── Маппинг доменных объектов в API-схемы (#4) ────────────────────────────────

def _map_dashboard(data: DashboardData) -> DashboardResponse:
    return DashboardResponse(
        total_documents=data.total_documents,
        awaiting_approval_count=data.awaiting_approval_count,
        ready_count=data.ready_count,
        relevance_percent=data.relevance_percent,
        activity_last_7_days=[
            DayActivity(date=d.date, analyzed=d.analyzed, approved=d.approved)
            for d in data.activity_last_7_days
        ],
    )


def _map_attention(items: list[AttentionItem]) -> list[AttentionDocumentItem]:
    return [
        AttentionDocumentItem(
            id=i.id,
            name=i.name,
            project_id=i.project_id,
            project_name=i.project_name,
            pending_suggestions=i.pending_suggestions,
            updated_at=i.updated_at,
        )
        for i in items
    ]


def _map_recent(items: list[RecentItem]) -> list[RecentDocumentItem]:
    return [
        RecentDocumentItem(
            id=i.id,
            name=i.name,
            project_id=i.project_id,
            project_name=i.project_name,
            status=i.status,
            last_opened_at=i.last_opened_at,
        )
        for i in items
    ]


# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> DashboardResponse:
    """Агрегаты рабочего пространства текущего пользователя."""
    data = await svc.get_dashboard(current_user.id)
    return _map_dashboard(data)


@router.get("/documents/attention", response_model=list[AttentionDocumentItem])
async def get_attention_documents(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> list[AttentionDocumentItem]:
    """Топ-4 документа со статусом awaiting_approval по кол-ву pending-правок."""
    items = await svc.get_attention_documents(current_user.id, limit=4)
    return _map_attention(items)


@router.get("/documents/recent", response_model=list[RecentDocumentItem])
async def get_recent_documents(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> list[RecentDocumentItem]:
    """5 последних документов, открытых текущим пользователем."""
    items = await svc.get_recent_documents(current_user.id, limit=5)
    return _map_recent(items)


@router.post(
    "/projects/{project_id}/documents/{document_id}/open",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["documents"],
)
async def track_document_open(
    document_id: uuid.UUID,
    # #3: get_allowed_project проверяет, что документ принадлежит текущему
    # пользователю, и предотвращает трекинг чужих документов.
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> None:
    """Трекинг открытия документа. Фронт вызывает при каждом открытии редактора."""
    await svc.track_open(current_user.id, document_id)
