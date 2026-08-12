"""
FastAPI-зависимости для аутентификации и авторизации.

get_allowed_project — единая точка принятия решения о доступе к проекту.
В MVP правило простое (admin видит всё, иначе только свой project.owner_id),
но весь остальной код обращается именно к этой функции, а не сравнивает id напрямую —
это то место, которое поменяется, когда появится таблица project_members и роли внутри проекта.
"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.dependencies import get_project_repository, get_user_repository
from app.domain.exceptions import InvalidTokenError
from app.infrastructure.db.models.enums import UserRole
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User
from app.infrastructure.db.repositories.project_repository import ProjectRepository
from app.infrastructure.db.repositories.user_repository import UserRepository
from app.infrastructure.security.jwt_handler import JWTHandler

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    user_repository: UserRepository = Depends(get_user_repository),
) -> User:
    jwt_handler = JWTHandler()
    try:
        payload = jwt_handler.decode_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await user_repository.get_by_id(uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Требуются права администратора")
    return current_user


async def get_allowed_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    project_repository: ProjectRepository = Depends(get_project_repository),
) -> Project:
    project = await project_repository.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден")

    if current_user.role != UserRole.ADMIN and project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к этому проекту")

    return project
