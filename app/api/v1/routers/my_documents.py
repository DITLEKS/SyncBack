"""
Глобальный список документов пользователя (P0-4).

GET /api/v1/documents — возвращает все документы текущего пользователя
вне зависимости от проекта, с агрегированными счётчиками правок.

Параметры:
  status      — фильтр по статусу документа (draft|in_progress|awaiting_approval|ready)
  search      — поиск по названию (регистронезависимый, подстрока)
  sort_by     — поле сортировки: updated_at (по умолчанию), created_at, title
  sort_dir    — направление: desc (по умолчанию) | asc
  limit       — кол-во элементов, 1..200, по умолчанию 50
  offset      — смещение, по умолчанию 0

Ответ (DocumentListPage):
  items[]:
    id, title, format, status, current_analysis_job_id,
    created_at, updated_at,
    project: { id, name },
    suggestions: { total, pending, accepted, rejected }
  total, limit, offset
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.api.schemas.document import (
    DocumentListItem,
    DocumentListPage,
    DocumentListProject,
    SuggestionCounters,
)
from app.core.dependencies import get_document_service
from app.domain.services.document_service import DocumentService
from app.infrastructure.db.models.enums import DocumentStatus
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/documents", tags=["my-documents"])


@router.get("", response_model=DocumentListPage)
async def list_my_documents(
    status: DocumentStatus | None = Query(None, description="Фильтр по статусу документа"),
    search: str | None = Query(None, max_length=200, description="Поиск по названию (подстрока)"),
    sort_by: Literal["created_at", "updated_at", "title"] = Query(
        "updated_at", description="Поле сортировки"
    ),
    sort_dir: Literal["asc", "desc"] = Query("desc", description="Направление сортировки"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentListPage:
    rows, total = await document_service.list_all_for_user(
        current_user.id,
        status=status,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )

    items = [
        DocumentListItem(
            id=row["document"].id,
            title=row["document"].title,
            format=row["document"].format,
            status=row["document"].status,
            current_analysis_job_id=row["document"].current_analysis_job_id,
            created_at=row["document"].created_at,
            updated_at=row["document"].updated_at,
            project=DocumentListProject(
                id=row["document"].project_id,
                name=row["project_name"],
            ),
            suggestions=SuggestionCounters(
                total=row["suggestions_total"],
                pending=row["suggestions_pending"],
                accepted=row["suggestions_accepted"],
                rejected=row["suggestions_rejected"],
            ),
        )
        for row in rows
    ]

    return DocumentListPage(items=items, total=total, limit=limit, offset=offset)
