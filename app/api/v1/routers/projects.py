"""
CRUD проектов. Листинг фильтруется по видимости (ProjectService), точечный доступ —
через get_allowed_project.

ДОБАВЛЕНО:
- PATCH /{project_id} — частичное обновление (переименование / изменение описания).
- DELETE /{project_id} — каскадное удаление документов (+ MinIO), источников, jobs.

I-4: GET /{project_id}?include=documents,sources
  Расширяет ProjectResponse без нового эндпоинта:
    include=sources   → project-scope источники проекта (SourceResponse[])
    include=documents → документы проекта (DocumentListItem[] с SourceBadge)
  Без include — поведение прежнее (лёгкий ответ, подходит для списка карточек).

  Допустимые значения include: "sources", "documents".
  Неизвестные значения игнорируются (graceful degradation).

  Пример для страницы проекта (1 запрос вместо 3):
    GET /projects/{id}?include=documents&include=sources
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentListItem, DocumentListProject, SuggestionCounters
from app.api.schemas.pagination import Page
from app.api.schemas.project import ProjectCreateRequest, ProjectResponse, ProjectUpdateRequest
from app.api.schemas.source import SourceBadge, SourceResponse
from app.core.dependencies import get_document_service, get_project_service, get_source_service
from app.domain.services.document_service import DocumentService
from app.domain.services.project_service import ProjectService
from app.domain.services.source_service import SourceService
from app.domain.value_objects import PaginationParams, SourceScopeVO
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/projects", tags=["projects"])

# I-4: допустимые значения ?include=
_VALID_INCLUDES = frozenset({"sources", "documents"})


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    current_user: User = Depends(get_current_user),
    project_service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    project = await project_service.create_project(current_user, payload.name, payload.description)
    return ProjectResponse.model_validate(project)


@router.get("", response_model=Page[ProjectResponse])
async def list_projects(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    project_service: ProjectService = Depends(get_project_service),
) -> Page[ProjectResponse]:
    projects, total = await project_service.list_projects_for_user(
        current_user, limit=limit, offset=offset
    )
    return Page[ProjectResponse](
        items=[ProjectResponse.model_validate(p) for p in projects],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Получить проект (с опциональным include)",
)
async def get_project(
    project: Project = Depends(get_allowed_project),
    include: Annotated[
        list[str],
        Query(
            alias="include",
            description=(
                "Список связанных данных для включения в ответ. "
                "Допустимые значения: sources, documents. "
                "Пример: ?include=documents&include=sources"
            ),
        ),
    ] = [],
    document_service: DocumentService = Depends(get_document_service),
    source_service: SourceService = Depends(get_source_service),
) -> ProjectResponse:
    """Получить проект по ID.

    Без ?include — лёгкий ответ (подходит для списка карточек, используется в GET /projects).

    С ?include=documents&include=sources — страница проекта одним запросом:
    - sources   → список project-scope источников проекта
    - documents → список документов проекта с бейджами их document-scope источников
    """
    includes = _VALID_INCLUDES.intersection(include)

    response = ProjectResponse.model_validate(project)

    # include=sources: project-scope источники (глобальные для всего проекта)
    if "sources" in includes:
        raw_sources = await source_service.list_sources_for_project(
            project.id, scope=SourceScopeVO.PROJECT
        )
        response.sources = [SourceResponse.model_validate(s) for s in raw_sources]

    # include=documents: документы проекта + их document-scope источники (бейджи)
    if "documents" in includes:
        pagination = PaginationParams(limit=200, offset=0)
        raw_docs, _ = await document_service.list_documents(project.id, pagination)

        # Подгружаем document-scope источники одним батч-запросом
        doc_ids = [d.id for d in raw_docs]
        sources_by_doc: dict[uuid.UUID, list] = {}
        if doc_ids:
            doc_sources = await source_service.list_sources_for_documents(
                project.id, doc_ids, scope=SourceScopeVO.DOCUMENT
            )
            for src in doc_sources:
                sources_by_doc.setdefault(src.document_id, []).append(src)

        response.documents = [
            DocumentListItem(
                id=d.id,
                name=d.name,
                format=d.format.value if hasattr(d.format, "value") else d.format,
                size_bytes=d.size_bytes,
                status=d.status.value if hasattr(d.status, "value") else d.status,
                current_analysis_job_id=d.current_analysis_job_id,
                created_at=d.uploaded_at,
                updated_at=d.uploaded_at,
                project=DocumentListProject(id=project.id, name=project.name),
                suggestions=SuggestionCounters(
                    total=0, pending=0, accepted=0, rejected=0
                ),
                sources=[
                    SourceBadge(id=s.id, name=s.name, type=s.type)
                    for s in sources_by_doc.get(d.id, [])
                ],
            )
            for d in raw_docs
        ]

    return response


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    payload: ProjectUpdateRequest,
    project: Project = Depends(get_allowed_project),
    project_service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    """Частичное обновление проекта (переименование, изменение описания)."""
    if payload.name is None and payload.description is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Необходимо указать хотя бы одно поле для обновления: name или description.",
        )
    updated = await project_service.update_project(
        project,
        name=payload.name,
        description=payload.description,
    )
    return ProjectResponse.model_validate(updated)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project: Project = Depends(get_allowed_project),
    project_service: ProjectService = Depends(get_project_service),
) -> None:
    """Каскадное удаление: документы (+ MinIO-файлы), источники (+ MinIO-файлы), jobs, suggestions."""
    await project_service.delete_project(project)
