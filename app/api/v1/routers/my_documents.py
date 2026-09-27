"""
Мои документы — список всех документов пользователя.

I-1: после list_all_for_user делается один батч-запрос
  list_sources_for_documents() и результат раскладывается
  в DocumentListItem.sources: list[SourceBadge].
  Фронт получает бейджи источников без доп-запросов.

Контракт sources в DocumentListItem:
  sources=None  — поле не запрашивалось (лёгкий листинг без источников).
  sources=[]    — запрашивалось, источников нет.
  sources=[...] — список бейджей.
  Данный эндпоинт всегда возвращает sources=[...] (никогда None),
  так как батч-запрос делается всегда. None используется будущими
  лёгкими GET /me/documents?include_sources=false эндпоинтами.
"""
import uuid
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.api.schemas.document import (
    DocumentListItem,
    DocumentListPage,
    DocumentListProject,
    SuggestionCounters,
)
from app.api.schemas.source import SourceBadge
from app.core.dependencies import get_document_service, get_source_service
from app.domain.services.document_service import DocumentService
from app.domain.services.source_service import SourceService
from app.domain.value_objects import DocumentStatusVO, PaginationParams
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/me/documents", tags=["my-documents"])


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
    source_service: SourceService = Depends(get_source_service),
) -> DocumentListPage:
    """Список всех документов текущего пользователя.

    I-1: DocumentListItem.sources заполняется через батч-запрос
    list_sources_for_documents. Возвращает sources=[...] (никогда None).
    См. контракт sources в docstring модуля.
    """
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

    # I-1: один батч-запрос для всех document-scope источников.
    # Документы пользователя могут принадлежать разным проектам — группируем
    # по project_id и делаем N запросов (обычно 1 для большинства пользователей).
    # Будущий рефактор: если понадобится единый запрос без группировки —
    # добавить list_by_document_ids_no_project в репозиторий.
    by_project: dict[uuid.UUID, list] = defaultdict(list)
    for row in rows:
        by_project[row["document"].project_id].append(row["document"])

    sources_by_doc: dict[uuid.UUID, list] = {}
    for project_id, docs in by_project.items():
        doc_ids = [d.id for d in docs]
        batch = await source_service.list_sources_for_documents(project_id, doc_ids)
        sources_by_doc.update(batch)

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
            # I-1: бейджи источников документа; никогда не None (батч всегда выполняется).
            # [] — если у документа нет привязанных document-scope источников.
            sources=[
                SourceBadge(
                    id=s.id,
                    name=s.name,
                    type=s.type,  # field_validator normalises enum → str
                )
                for s in sources_by_doc.get(row["document"].id, [])
            ],
        )
        for row in rows
    ]

    return DocumentListPage(items=items, total=total, limit=limit, offset=offset)
