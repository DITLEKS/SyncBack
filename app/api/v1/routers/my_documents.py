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
from app.domain.value_objects import DocumentStatusVO, PaginationParams
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/documents", tags=["my-documents"])


@router.get("", response_model=DocumentListPage)
async def list_my_documents(
    status: DocumentStatusVO | None = Query(
        None,
        description=(
            "Фильтр по статусу документа. "
            "Допустимые значения: draft | in_progress | awaiting_approval | ready. "
            "Если не передан — возвращаются документы во всех статусах."
        ),
    ),
    outdated: bool = Query(
        False,
        description=(
            "Если true: возвращать только документы у которых есть хотя бы одна "
            "правка в статусе pending. Можно комбинировать с параметром status."
        ),
    ),
    search: str | None = Query(None, max_length=200, description="Поиск по названию (подстрока)"),
    sort_by: Literal["created_at", "updated_at", "name"] = Query(
        "updated_at", description="Поле сортировки"
    ),
    sort_dir: Literal["asc", "desc"] = Query("desc", description="Направление сортировки"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentListPage:
    pagination = PaginationParams(limit=limit, offset=offset)
    rows, total = await document_service.list_all_for_user(
        current_user.id,
        status=status,
        outdated=outdated,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        pagination=pagination,
    )

    items = [
        DocumentListItem(
            id=row["document"].id,
            name=row["document"].name,
            format=row["document"].format,
            size_bytes=row["document"].size_bytes,
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
