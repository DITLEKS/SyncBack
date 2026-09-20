"""
Дашборд / рабочее пространство.

P0-#1  GET  /dashboard            — агрегаты: кол-во документов, awaiting, ready, актуальность, активность 7 дней
P0-#2  GET  /documents/attention  — топ-4 документа в awaiting_approval по убыванию pending-правок
P0-#3  GET  /documents/recent     — 5 последних открытых текущим пользователем
P0-#3  POST /documents/{id}/open  — трекинг открытия документа (обновляет last_opened_at)
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.api.schemas.dashboard import (
    AttentionDocumentItem,
    DashboardResponse,
    DayActivity,
    RecentDocumentItem,
)
from app.core.dependencies import get_dashboard_service
from app.domain.services.dashboard_service import DashboardService
from app.infrastructure.db.models.user import User

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> DashboardResponse:
    """Агрегаты рабочего пространства текущего пользователя."""
    return await svc.get_dashboard(current_user.id)


@router.get("/documents/attention", response_model=list[AttentionDocumentItem])
async def get_attention_documents(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> list[AttentionDocumentItem]:
    """Топ-4 документа со статусом awaiting_approval, отсортированные по кол-ву
    pending-правок (DESC). Отображаются в блоке «Требуют внимания»."""
    return await svc.get_attention_documents(current_user.id, limit=4)


@router.get("/documents/recent", response_model=list[RecentDocumentItem])
async def get_recent_documents(
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> list[RecentDocumentItem]:
    """5 последних документов, открытых текущим пользователем (по last_opened_at DESC)."""
    return await svc.get_recent_documents(current_user.id, limit=5)


@router.post(
    "/projects/{project_id}/documents/{document_id}/open",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["documents"],
)
async def track_document_open(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    svc: DashboardService = Depends(get_dashboard_service),
) -> None:
    """Трекинг открытия документа. Фронт вызывает при каждом открытии редактора.
    Обновляет last_opened_at для пары (user_id, document_id)."""
    await svc.track_open(current_user.id, document_id)
