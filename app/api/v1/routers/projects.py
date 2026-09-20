"""
CRUD проектов. Листинг фильтруется по видимости (ProjectService), точечный доступ —
через get_allowed_project.

ДОБАВЛЕНО:
- PATCH /{project_id} — частичное обновление (переименование / изменение описания).
- DELETE /{project_id} — каскадное удаление документов (+ MinIO), источников, jobs.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.pagination import Page
from app.api.schemas.project import ProjectCreateRequest, ProjectResponse, ProjectUpdateRequest
from app.core.dependencies import get_project_service
from app.domain.services.project_service import ProjectService
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/projects", tags=["projects"])


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
    projects, total = await project_service.list_projects_for_user(current_user, limit=limit, offset=offset)
    return Page[ProjectResponse](
        items=[ProjectResponse.model_validate(p) for p in projects], total=total, limit=limit, offset=offset
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project: Project = Depends(get_allowed_project)) -> ProjectResponse:
    return ProjectResponse.model_validate(project)


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
