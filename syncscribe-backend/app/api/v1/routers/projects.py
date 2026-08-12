"""
CRUD проектов. Листинг фильтруется по видимости (ProjectService), точечный доступ —
через get_allowed_project.
"""

from fastapi import APIRouter, Depends, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.project import ProjectCreateRequest, ProjectResponse
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
    project = await project_service.create_project(current_user, payload.name)
    return ProjectResponse.model_validate(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    current_user: User = Depends(get_current_user),
    project_service: ProjectService = Depends(get_project_service),
) -> list[ProjectResponse]:
    projects = await project_service.list_projects_for_user(current_user)
    return [ProjectResponse.model_validate(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project: Project = Depends(get_allowed_project)) -> ProjectResponse:
    return ProjectResponse.model_validate(project)
